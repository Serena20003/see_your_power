from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import numpy as np
from .contracts import GridSpec,StaticManifest,WEATHER_BANDS,WEATHER_UNITS,WEATHER_SEMANTICS,NODATA,HOUR,canonical,utc
from .static import load_static,supported_land,coverage_summary
from .rasters import validate_cog

LATEST='raster/v1/latest.json'


@dataclass
class PublicationResult:
    status: str
    batch_id: str


def _time(value):return utc(datetime.fromisoformat(value.replace('Z','+00:00')))


def freshness(manifest):
    return tuple(_time(manifest[k]) for k in ('target_start','selected_run','cutoff'))


def validate_batch(manifest,store):
    if manifest.get('schema_version')!='raster-batch/v1' or manifest.get('quality_status')!='complete':raise ValueError('complete batch schema required')
    if manifest.get('value_kind')!='heuristic_score' or manifest.get('calibrated') is not False or manifest.get('asset_conditioned') is not False:
        raise ValueError('only uncalibrated environmental risk scores are supported')
    if 'probability' in manifest:raise ValueError('MVP cannot publish probability')
    grid=GridSpec.model_validate(manifest['grid'])
    static_obj=manifest['static_manifest'];raw,_=store.read(static_obj['key'])
    if raw is None or hashlib.sha256(raw).hexdigest()!=static_obj['sha256']:raise ValueError('static manifest checksum mismatch')
    static=StaticManifest.model_validate_json(raw)
    if static.static_version!=manifest['static_version']:raise ValueError('static version mismatch')
    if static.fixture!=manifest.get('fixture',False):raise ValueError('fixture/live static mixing forbidden')
    context=load_static(static,grid,store)
    policy=manifest.get('coverage_policy','complete_land')
    if policy not in ('complete_land','supported_land'):raise ValueError('unknown coverage policy')
    domain=supported_land(context,allow_gaps=policy=='supported_land')
    if policy=='supported_land' or 'coverage_summary' in manifest:
        if manifest.get('coverage_summary')!=coverage_summary(context):raise ValueError('coverage summary does not match static inputs')
    intervals=manifest['intervals']
    if len(intervals)!=72:raise ValueError('exactly 72 target intervals required')
    start=_time(manifest['target_start'])
    from .source import ceil_hour
    if start!=ceil_hour(_time(manifest['cutoff'])):raise ValueError('target start/cutoff mismatch')
    inputs=manifest['inputs'];raw,_=store.read(inputs['key'])
    if raw is None or hashlib.sha256(raw).hexdigest()!=inputs['sha256']:raise ValueError('inputs manifest checksum mismatch')
    used=set()
    with tempfile.TemporaryDirectory() as tmp:
        for h,item in enumerate(intervals):
            begin=start+h*HOUR;end=begin+HOUR
            if _time(item['start'])!=begin or _time(item['end'])!=end:raise ValueError('noncontiguous/duplicate hourly intervals')
            for kind,bands,units in [('weather',WEATHER_BANDS,WEATHER_UNITS),('risk',['risk_score'],['1'])]:
                obj=item[kind];key=obj['key']
                if key in used:raise ValueError('duplicate raster object')
                used.add(key)
                raw,_=store.read(key)
                if raw is None or hashlib.sha256(raw).hexdigest()!=obj['sha256']:raise ValueError('raster checksum mismatch')
                path=Path(tmp)/(kind+'.tif');path.write_bytes(raw)
                a,tags=validate_cog(path,grid,bands,units)
                if tags.get('interval_start')!=begin.isoformat() or tags.get('interval_end')!=end.isoformat():raise ValueError('raster interval tag mismatch')
                counts=[int(np.sum(b!=NODATA)) for b in a]
                if counts!=obj['valid_cell_counts']:raise ValueError('valid-cell count mismatch')
                if kind=='weather':
                    if np.any(a==NODATA):raise ValueError('unexpectedly partial weather grid')
                    if (a[0]<0).any() or (a[2]<0).any():raise ValueError('negative precipitation/wind')
                    for band,sem in zip(WEATHER_BANDS,WEATHER_SEMANTICS):
                        if tags.get(band+'_temporal_semantics')!=sem:raise ValueError('invalid temporal semantics')
                else:
                    land=domain
                    if np.any(a[0,~land]!=NODATA) or np.any((a[0,land]<0)|(a[0,land]>1)):
                        raise ValueError('invalid/partial risk coverage or range')
                    if tags.get('value_kind')!='heuristic_score' or tags.get('calibrated')!='false' or tags.get('asset_conditioned')!='false':raise ValueError('invalid score labels')
    return context


def _publish_batch(manifest,store,expected_latest_generation):
    validate_batch(manifest,store)
    key=f"raster/v1/runs/{manifest['batch_id']}/manifest.json"
    from .storage import check_key
    check_key(key)
    store.put(key,canonical(manifest))
    current,generation=store.read(LATEST)
    previous=None
    if current:
        pointer=json.loads(current)
        if pointer['batch_id']==manifest['batch_id']:
            if pointer['sha256']!=hashlib.sha256(canonical(manifest)).hexdigest():raise ValueError('batch ID collision')
            return PublicationResult('already_published',manifest['batch_id'])
        previous={k:pointer[k] for k in ('batch_id','manifest_key','sha256')}
        old_raw,_=store.read(pointer['manifest_key'])
        if old_raw is None:raise ValueError('latest references missing manifest')
        if freshness(json.loads(old_raw))>freshness(manifest):return PublicationResult('superseded',manifest['batch_id'])
    pointer={'schema_version':'raster-latest/v2','batch_id':manifest['batch_id'],'previous':previous,
        'manifest_key':key,'sha256':hashlib.sha256(canonical(manifest)).hexdigest(),
        'target_start':manifest['target_start'],'cutoff':manifest['cutoff'],
        'selected_run':manifest['selected_run'],'published_at':manifest['published_at']}
    if store.replace_pointer(LATEST,canonical(pointer),expected_latest_generation):return PublicationResult('published',manifest['batch_id'])
    # A competing publisher won. Do not retry blindly with its generation.
    current,_=store.read(LATEST)
    if current and json.loads(current)['batch_id']==manifest['batch_id']:return PublicationResult('already_published',manifest['batch_id'])
    return PublicationResult('superseded',manifest['batch_id'])


def publish_batch(manifest,store,expected_latest_generation):
    # GC and publishing share a reference lock. An old archived object cannot
    # be deleted between validation and publishing a fresh reference to it.
    with store.reference_lock():
        result=_publish_batch(manifest,store,expected_latest_generation)
    # Reacquire the reference lock inside GC and inspect the latest pointer.
    # If another publisher has advanced it, its current/recovery pair wins.
    from .retention import collect_garbage
    try:collect_garbage(store,dry_run=False)
    except Exception as exc:
        # Publication already succeeded; cleanup failure must not relabel a
        # valid published batch as incomplete. A later run retries cleanup.
        import logging
        logging.getLogger('raster_pipeline').warning(json.dumps({
            'stage':'cleanup_failed','failure_reason':f'{type(exc).__name__}: {exc}'}))
    return result
