"""Batch orchestration. Live and explicit fixture entry points never fall back."""
from datetime import datetime,timezone
import hashlib
import json
import logging
from pathlib import Path
import resource
import tempfile
import time
import uuid
import numpy as np
from .contracts import (GridSpec,InputInterval,InputInventory,SourceSelection,WeatherCube,
    StaticContext,StaticManifest,HOUR,NODATA,canonical,digest,utc)
from .config import PipelineConfig
from .source import discover_run,ceil_hour
from .download import download_inputs
from .weather import prepare_weather
from .windows import iter_windows,target_intervals
from .risk import RiskCalculator,RiskConfig,load_risk
from .static import load_static,save_static,supported_land,coverage_summary
from .rasters import write_weather_cog,write_risk_cog,validate_cog
from .publish import publish_batch,LATEST

LOG=logging.getLogger('raster_pipeline')


def event(stage,**data):LOG.info(json.dumps({'stage':stage,**data},default=str))


def read_json(store,key):
    data,_=store.read(key)
    if data is None:raise ValueError(f'missing object: {key}')
    return json.loads(data)


def _static(config,store,fixture):
    if not config.static_manifest:raise ValueError('pinned static manifest required')
    raw,_=store.read(config.static_manifest)
    if raw is None:raise ValueError('pinned static manifest not found in output store')
    manifest=StaticManifest.model_validate_json(raw)
    if manifest.fixture!=fixture:raise ValueError('live/fixture static mixing forbidden')
    context=load_static(manifest,config.grid,store)
    supported_land(context,allow_gaps=config.allow_static_gaps)
    return context,{'key':config.static_manifest,'sha256':hashlib.sha256(raw).hexdigest()}


def batch_identity(selection,fields,config,risk_config,static_ref,fixture,weather_recipe=None):
    from .decode import tool_versions
    import scipy,pyproj
    recipe={'version':'weather-normalization-v4','native_grid_sha256':config.native_grid_sha256,
        'precipitation_remap':'cdo_conservative_float64',
        'precipitation_deaccumulation':'decoded_GRIB_packing_error_sum_with_1e-4_mm_floor',
        'scalar_remap':'fixture_exact_centers' if fixture else 'triangle_barycentric_v1_for_ICON_regular_ll_bilinear',
        'projection':'EPSG:5070','padding_degrees':2,
        'tools':{**tool_versions(),'scipy':scipy.__version__,'pyproj':pyproj.__version__}}
    if weather_recipe is not None:recipe=weather_recipe
    identity={'selected_run':selection.selected_run.isoformat(),
        'target_start':selection.target_start.isoformat(),'grid':config.grid.model_dump(),
        'lookback_hours':config.lookback_hours,'risk_config':risk_config.model_dump(mode='json'),
        'static_manifest':static_ref,'fixture':fixture,'weather_recipe':recipe,
        'coverage_policy':'supported_land' if config.allow_static_gaps else 'complete_land',
        'inputs':[{k:f.get(k) for k in ('run','field','start_step','end_step','sha256','grid_id','units','level','level_type','packing','vector_reference','url')}
                  for f in fields]}
    return digest(identity)[:32],recipe


def produce(cube,selection,inventory,config,risk_config,context,static_ref,store,*,expected_generation,fixture=False,reuse_weather=None,weather_recipe=None):
    domain=supported_land(context,allow_gaps=config.allow_static_gaps)
    batch_id,recipe=batch_identity(selection,inventory.get('fields',[]),config,risk_config,static_ref,fixture,weather_recipe)
    prefix=f'raster/v1/runs/{batch_id}'
    previous,_=store.read(prefix+'/manifest.json')
    if previous:
        manifest=json.loads(previous);publish_batch(manifest,store,expected_generation);return manifest
    inputs_key=prefix+'/inputs.json';inputs_raw,_=store.read(inputs_key)
    if inputs_raw is not None:
        # The first successful inventory is immutable provenance for this
        # semantic batch. Reuse it after a crash instead of replacing paths.
        inventory=json.loads(inputs_raw)
        selection=SourceSelection.model_validate(inventory['selection'])
    else:
        inputs_raw=canonical(inventory);store.put(inputs_key,inputs_raw)
    entries=[];calculator=RiskCalculator();diagnostics=[]
    starts={i.start:n for n,i in enumerate(cube.intervals)}
    # Persist supporting normalized hours for offline replay with the exact lookback.
    # These are supporting inputs, never extra target raster entries.
    support=[]
    with tempfile.TemporaryDirectory() as tmp:
        for n,interval in enumerate(cube.intervals):
            if interval.start>=selection.target_start:continue
            file=Path(tmp)/'history.tif'
            info=write_weather_cog(file,np.where(cube.valid[n],cube.values[n],NODATA),config.grid,interval)
            key=prefix+'/supporting/'+interval.start.strftime('%Y%m%dT%H%M%SZ')+'.tif'
            store.put(key,file.read_bytes());support.append({'start':interval.start.isoformat(),'end':interval.end.isoformat(),
                'run':interval.run.isoformat(),'weather':{**info,'key':key},'provenance':cube.provenance[n] if cube.provenance else {}})
        for h,window in enumerate(iter_windows(cube,target_intervals(selection.target_start),config.lookback_hours)):
            interval=window.target;n=starts.get(interval.start)
            if n is None:raise ValueError('missing target hour')
            if not cube.valid[n].all():raise ValueError('incomplete weather grid')
            result=calculator.calculate(window,context,risk_config)
            if not np.array_equal(result.valid,domain):raise ValueError('incomplete risk history/environmental coverage')
            label=interval.start.strftime('%Y%m%dT%H%M%SZ')
            if reuse_weather is not None:weather=reuse_weather[h]['weather']
            else:
                file=Path(tmp)/'weather.tif';info=write_weather_cog(file,cube.values[n],config.grid,interval)
                key=f'{prefix}/weather/{label}.tif';store.put(key,file.read_bytes());weather={**info,'key':key}
            file=Path(tmp)/'risk.tif';info=write_risk_cog(file,result,config.grid,interval)
            key=f'{prefix}/risk/{label}.tif';store.put(key,file.read_bytes())
            entries.append({'start':interval.start.isoformat(),'end':interval.end.isoformat(),
                'run':cube.intervals[n].run.isoformat(),'weather':weather,'risk':{**info,'key':key},
                'input_intervals':[x.model_dump(mode='json') if x else None for x in window.intervals]})
            diagnostics.append({'start':interval.start.isoformat(),**{k:{'min':float(v[result.valid].min()),
                'max':float(v[result.valid].max()),'mean':float(v[result.valid].mean())} for k,v in result.diagnostics.items()}})
    store.put(prefix+'/diagnostics.json',canonical(diagnostics))
    manifest={'schema_version':'raster-batch/v1','batch_id':batch_id,'grid':config.grid.model_dump(),
        'cutoff':selection.cutoff.isoformat(),'target_start':selection.target_start.isoformat(),
        'target_end':(selection.target_start+72*HOUR).isoformat(),'selected_run':selection.selected_run.isoformat(),
        'issue_time':selection.selected_run.isoformat(),'published_at':datetime.now(timezone.utc).isoformat(),
        'supporting_runs':sorted({x.run.isoformat() for x in cube.intervals}),
        'source_first_seen':min((f.first_seen.isoformat() for f in selection.fields),default=None),
        'lookback_hours':config.lookback_hours,'intervals':entries,'supporting_intervals':support,
        'static_manifest':static_ref,'static_version':context.version,
        'canopy_vintage':context.manifest.get('sources',{}).get('canopy',{}).get('vintage'),
        'coverage_object':context.manifest.get('objects',{}).get('coverage'),
        'risk_config':risk_config.model_dump(mode='json'),'risk_config_hash':digest(risk_config),
        'configuration_hash':digest(config.model_dump(exclude={'catalog'})),'weather_recipe':recipe,
        'value_kind':'heuristic_score','calibrated':False,'asset_conditioned':False,
        'quality_status':'complete','fixture':fixture,
        'coverage_policy':'supported_land' if config.allow_static_gaps else 'complete_land',
        'coverage_summary':coverage_summary(context),
        'inputs':{'key':inputs_key,'sha256':hashlib.sha256(inputs_raw).hexdigest()},
        'bands':{'weather':[{'name':n,'unit':u,'temporal_semantics':t} for n,u,t in zip(
            ('precipitation_mm','temperature_c','wind_speed_ms'),('mm','degC','m s-1'),('interval_total','endpoint_sample','endpoint_sample'))],
            'risk':[{'name':'risk_score','unit':'1','value_kind':'heuristic_score'}]},
        'attribution':['Deutscher Wetterdienst (DWD), ICON Global','Static data attribution in static manifest'],
        'diagnostics_key':prefix+'/diagnostics.json'}
    result=publish_batch(manifest,store,expected_generation)
    event('publication',batch_id=batch_id,status=result.status)
    return manifest


def run(config,cutoff,store,*,work_dir=None,fixture_dir=None,dry_run=False):
    started=time.monotonic();attempt_id=uuid.uuid4().hex;cutoff=utc(cutoff)
    record={'attempt_id':attempt_id,'cutoff':cutoff.isoformat(),'started_at':datetime.now(timezone.utc).isoformat(),
        'configuration_hash':digest(config.model_dump(exclude={'catalog'})),'fixture':fixture_dir is not None}
    _,generation=store.read(LATEST)
    try:
        context,static_ref=_static(config,store,fixture_dir is not None)
        selection=discover_run(cutoff,ceil_hour(cutoff),config)
        event('discovery',run=selection.selected_run,intervals=len(selection.intervals),missing_history=len(selection.missing_history))
        record.update(source_age_hours=(cutoff-selection.selected_run)/HOUR,input_count=len(selection.fields),
            missing_history=len(selection.missing_history))
        if selection.missing_history:raise ValueError('missing required lookback history')
        if dry_run:
            record['status']='dry_run';return {'status':'dry_run','selection':selection.model_dump(mode='json')}
        risk_config=load_risk(config.risk_config)
        if all(f.sha256 for f in selection.fields):
            batch_id,_=batch_identity(selection,[f.model_dump(mode='json') for f in selection.fields],config,risk_config,static_ref,fixture_dir is not None)
            previous,_=store.read(f'raster/v1/runs/{batch_id}/manifest.json')
            if previous:
                manifest=json.loads(previous);publication=publish_batch(manifest,store,generation)
                record.update(status='complete',batch_id=batch_id,reused=True,publication_status=publication.status,download_bytes=0)
                return manifest
        root=Path(work_dir or tempfile.mkdtemp(prefix='raster-'));root.mkdir(parents=True,exist_ok=True)
        if fixture_dir is not None:
            from .fixtures import fixture_inventory,fixture_remap
            inventory=fixture_inventory(selection,fixture_dir)
            remapper=lambda f:fixture_remap(f,config.grid)
        else:
            inventory=download_inputs(selection,root/'raw',concurrency=config.concurrency,retries=config.retries,timeout=config.timeout_seconds)
            from .remap import CDORemapper
            remapper=CDORemapper(config.grid,root/'weights',config.native_grid,config.native_grid_sha256)
        event('download',bytes=inventory.downloaded_bytes,files=len(inventory.fields))
        cube=prepare_weather(inventory,config.grid,remapper=remapper,work_dir=root/attempt_id)
        inputs=inventory.model_dump(mode='json')
        # Paths and retrieval timestamps are provenance, not semantic input identity;
        # archive the exact inventory and make repeat runs use the same cache.
        inputs['remaps']=getattr(remapper,'records',[])
        manifest=produce(cube,selection,inputs,config,risk_config,context,static_ref,store,
            expected_generation=generation,fixture=fixture_dir is not None)
        record.update(status='complete',batch_id=manifest['batch_id'],download_bytes=inventory.downloaded_bytes,
            weather_count=72,risk_count=72,output_bytes=sum(e[k]['bytes'] for e in manifest['intervals'] for k in ('weather','risk')))
        return manifest
    except Exception as exc:
        record.update(status='incomplete',failure_reason=f'{type(exc).__name__}: {exc}')
        event('failed',**record);raise
    finally:
        record.update(wall_seconds=time.monotonic()-started,peak_rss_native_units=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            finished_at=datetime.now(timezone.utc).isoformat())
        store.put(f'raster/v1/attempts/{attempt_id}.json',canonical(record))
        event('attempt',**record)


def fixture_static(grid,store):
    shape=grid.shape;land=np.ones(shape,dtype=bool)
    cov=np.ones((3,*shape),dtype='uint8');cov[2]=0;cov[2,:,0]=1
    exposure=np.full((4,*shape),NODATA,dtype='float32');exposure[:,:,0]=0
    s=StaticContext(grid,'fixture-'+digest(grid)[:16],np.full(shape,.5,dtype='float32'),
        np.ones(shape,dtype='float32'),land,exposure,cov)
    manifest=save_static(s,{'canopy':{'vintage':'synthetic-test-only','attribution':'CC0 fixture'}},
        {'version':'fixture-v1'},store,fixture=True)
    return manifest


def run_fixture(cutoff,store,*,risk_config=None,missing_hour=None,dry_run=False):
    from .fixtures import generate_fixture,load_fixture_config
    with tempfile.TemporaryDirectory() as tmp:
        grid=GridSpec(grid_id='fixture-two-cells-v1',west=-120,south=34,east=-118,north=35,resolution=1)
        generate_fixture(Path(tmp),cutoff,grid,missing_hour=missing_hour)
        static=fixture_static(grid,store)
        cfg=load_fixture_config(tmp)
        path=Path(tmp)/'risk.json';path.write_bytes(canonical(risk_config or RiskConfig()))
        cfg=cfg.model_copy(update={'static_manifest':f'raster/v1/static/{static.static_version}/manifest.json','risk_config':str(path)})
        return run(cfg,cutoff,store,fixture_dir=tmp,work_dir=Path(tmp)/'work',dry_run=dry_run)


def recalculate(weather_manifest,store,risk_config,*,static_manifest=None,allow_static_gaps=None):
    from .contracts import WEATHER_BANDS,WEATHER_UNITS
    from .publish import validate_batch
    validate_batch(weather_manifest,store)
    m=weather_manifest;grid=GridSpec.model_validate(m['grid'])
    allow_gaps=m.get('coverage_policy')=='supported_land' if allow_static_gaps is None else allow_static_gaps
    cfg=PipelineConfig(grid=grid,lookback_hours=m['lookback_hours'],static_manifest=static_manifest or m['static_manifest']['key'],allow_static_gaps=allow_gaps)
    context,static_ref=_static(cfg,store,m['fixture'])
    items=m['supporting_intervals']+m['intervals'];intervals=[];provenance=[]
    with tempfile.TemporaryDirectory() as tmp:
        values=np.lib.format.open_memmap(Path(tmp)/'replay.npy',mode='w+',dtype='float32',shape=(len(items),3,*grid.shape))
        valid=np.lib.format.open_memmap(Path(tmp)/'replay-valid.npy',mode='w+',dtype='bool',shape=values.shape)
        for n,item in enumerate(items):
            obj=item['weather'];raw,_=store.read(obj['key'])
            if raw is None or hashlib.sha256(raw).hexdigest()!=obj['sha256']:raise ValueError('archived weather checksum mismatch')
            path=Path(tmp)/'weather.tif';path.write_bytes(raw)
            a,_=validate_cog(path,grid,WEATHER_BANDS,WEATHER_UNITS);values[n]=a;valid[n]=a!=NODATA
            intervals.append(InputInterval(start=item['start'],end=item['end'],run=item['run']))
            provenance.append(item.get('provenance',{'archived_object':obj}))
        selection=SourceSelection(cutoff=m['cutoff'],target_start=m['target_start'],selected_run=m['selected_run'],intervals=intervals,fields=[])
        cube=WeatherCube(grid,values,intervals,valid,provenance)
        inputs=read_json(store,m['inputs']['key'])
        _,generation=store.read(LATEST)
        return produce(cube,selection,inputs,cfg,risk_config,context,static_ref,store,
            expected_generation=generation,fixture=m['fixture'],reuse_weather=m['intervals'],weather_recipe=m['weather_recipe'])
