"""Independent immutable output namespace, completeness checks and CAS rollback."""
from dataclasses import dataclass
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import tempfile
import numpy as np
from raster_pipeline.contracts import canonical,NODATA
from raster_pipeline.rasters import validate_cog
from .contracts import PredictionBatch, time
from .storage import checked_read
from .artifacts import load_bundle
from .splits import features_from_bytes
from .predict import approved_scopes
from .archive import load_archive
from .features import build_features
from .train import raw_margins
from .support import assess_support
from .calibration import calibrated_array

LATEST='outage/v1/latest.json'
LATEST_TEST='outage/v1/testing/latest.json'

@dataclass(frozen=True)
class PublicationResult:
    status:str
    batch_id:str


def validate_batch(batch,store,*,test_mode=False):
    # Revalidate even objects produced with model_copy, not only decoded JSON.
    batch=PredictionBatch.model_validate_json(batch.model_dump_json())
    if batch.fixture!=test_mode:raise ValueError('fixture/live publication mixing forbidden')
    archive=load_archive(store,batch.archive,test_mode=test_mode)
    upstream=archive.manifest
    if batch.cutoff!=time(upstream['cutoff']) or batch.selected_run!=time(upstream['selected_run']) or batch.target_start!=time(upstream['target_start']) or batch.grid!=archive.grid:
        raise ValueError('prediction timestamp/grid lineage mismatch')
    if batch.stale_after_hours!=2:raise ValueError('incompatible freshness policy')
    model=load_bundle(batch.model,store,test_mode=test_mode)
    features=features_from_bytes(checked_read(store,batch.features))
    if features.fixture!=test_mode or features.names!=model.feature_names:raise ValueError('published feature schema/fixture mismatch')
    if features.provenance['archive_id']!=batch.archive.archive_id:raise ValueError('feature/archive identity mismatch')
    rebuilt=build_features(archive)
    if features.keys!=rebuilt.keys or features.metadata!=rebuilt.metadata or features.provenance!=rebuilt.provenance or not np.array_equal(features.eligible,rebuilt.eligible) or not np.allclose(features.values,rebuilt.values,equal_nan=True,rtol=0,atol=0):
        raise ValueError('persisted features do not reproduce archived inputs')
    support=assess_support(features,model.support,approved_scopes(model),batch.cutoff,now=batch.published_at,stale_after_hours=batch.stale_after_hours)
    margins=raw_margins(model,features)
    scopes=approved_scopes(model);allowed={(s['grid_id'],r,c,s['horizon_id'],s['season']) for s in scopes for r,c in s.get('cells',[])}
    all_arrays={}
    with tempfile.TemporaryDirectory() as tmp:
        for window in batch.windows:
            arrays={}
            for kind,band,unit,coverage in [('probability','outage_probability','1',False),('status','probability_status','code',True),('fallback','peak_hourly_weather_risk_score','1',False)]:
                path=Path(tmp)/(kind+'.tif');path.write_bytes(checked_read(store,window[kind]))
                values,tags=validate_cog(path,batch.grid,[band],[unit],coverage=coverage)
                if tags['interval_start']!=window['start'] or tags['interval_end']!=window['end']:raise ValueError('output interval mismatch')
                arrays[kind]=values[0]
            p,status,fallback=(arrays[k] for k in ('probability','status','fallback'))
            if not np.isin(status,range(6)).all():raise ValueError('invalid status codes')
            if np.any((status==1)&((p<0)|(p>1))) or np.any((status!=1)&(p!=NODATA)):raise ValueError('probability/status mismatch')
            if np.any((fallback!=NODATA)&((fallback<0)|(fallback>1))):raise ValueError('invalid weather score range')
            if np.any(np.isin(status,[0,5])&(fallback!=NODATA)):raise ValueError('outside/stale fallback must be unavailable')
            horizon=window['horizon_id']
            rows=[(i,k) for i,k in enumerate(features.keys) if k.horizon_id==horizon]
            expected_keys={(batch.archive.archive_id,batch.grid.grid_id,r,c,horizon) for r in range(batch.grid.height) for c in range(batch.grid.width)}
            actual_keys={(k.archive_id,k.grid_id,k.row,k.col,k.horizon_id) for _,k in rows}
            if actual_keys!=expected_keys:raise ValueError('incomplete or invalid feature grid keys')
            idx=np.asarray([i for i,_ in rows],int)
            if not np.array_equal(status,support['status'][idx].reshape(batch.grid.shape)):raise ValueError('support/status reconstruction mismatch')
            selected=idx[support['status'][idx]==1]
            expected_probability=calibrated_array(model.calibrations[horizon],margins[selected],[support['region_ids'][i] for i in selected])
            if not np.allclose(p[status==1],expected_probability,atol=1e-6,rtol=0):raise ValueError('probability reconstruction mismatch')
            offset={'0_24':0,'24_48':24,'48_72':48}[horizon]
            hourly=archive.fallback[offset:offset+24]
            valid=(hourly!=NODATA).all(0)&np.isfinite(hourly).all(0)&archive.static.analysis_mask
            stale=(batch.published_at-batch.cutoff).total_seconds()>7200 or batch.cutoff>batch.published_at
            expected_fallback=np.where(valid & (not stale),hourly.max(0),NODATA)
            if not np.array_equal(fallback,expected_fallback):raise ValueError('weather fallback reconstruction mismatch')
            if len(rows)!=batch.grid.width*batch.grid.height:raise ValueError('incomplete feature rows')
            for i,key in rows:
                s=status[key.row,key.col];meta=features.metadata[i]
                if (s==0)!= (not meta['analysis_land']):raise ValueError('land/status mismatch')
                if s==1 and (key.grid_id,key.row,key.col,key.horizon_id,meta['season']) not in allowed:raise ValueError('probability lacks final-test validation scope')
            all_arrays[horizon]=arrays
    for obj in batch.explanations:
        record=json.loads(checked_read(store,obj));sha=record.pop('sha256')
        from raster_pipeline.contracts import digest
        if digest(record)!=sha:raise ValueError('explanation hash mismatch')
        from .contracts import FeatureRow,RowKey
        from .explain import explain
        key=RowKey.model_validate(record['row_key']);lookup={k.id:i for i,k in enumerate(features.keys)}
        if key.id not in lookup:raise ValueError('explanation row absent from features')
        i=lookup[key.id]
        reconstructed=explain(model,FeatureRow(key=key,names=features.names,values=tuple(features.values[i]),region=support['region_ids'][i],provenance=features.provenance,status=support['reasons'][i]))
        if reconstructed['sha256']!=sha:raise ValueError('persisted explanation differs from model reconstruction')
        if abs(record['base_raw_margin']+sum(record['raw_contributions'].values())-record['raw_margin'])>1e-6:raise ValueError('explanation reconstruction mismatch')
    return all_arrays


def _key(batch):return f'outage/v1/{"testing/" if batch.fixture else ""}runs/{batch.batch_id}/manifest.json'


def publish_batch(batch,store,expected_generation,*,test_mode=False):
    validate_batch(batch,store,test_mode=test_mode)
    if not test_mode and consumer_ages(batch)['stale'] and any(w['status_counts'].get('1',0) or w['status_counts'].get('2',0) or w['status_counts'].get('3',0) or w['status_counts'].get('4',0) for w in batch.windows):
        raise ValueError('forecast became stale before publication; rescore with original cutoff')
    raw=canonical(batch);key=_key(batch);store.put(key,raw)
    latest=LATEST_TEST if test_mode else LATEST
    current,generation=store.read(latest);previous=None
    if current:
        pointer=json.loads(current)
        if pointer['batch_id']==batch.batch_id:
            if pointer['sha256']!=hashlib.sha256(raw).hexdigest():raise ValueError('prediction batch ID collision')
            return PublicationResult('already_published',batch.batch_id)
        old=PredictionBatch.model_validate_json(checked_read(store,{'key':pointer['manifest_key'],'sha256':pointer['sha256']}))
        if (old.cutoff,old.selected_run,old.published_at)>(batch.cutoff,batch.selected_run,batch.published_at):
            return PublicationResult('superseded',batch.batch_id)
        previous={k:pointer[k] for k in ('batch_id','manifest_key','sha256')}
    pointer={'schema_version':'outage-latest/v1','batch_id':batch.batch_id,'manifest_key':key,
        'sha256':hashlib.sha256(raw).hexdigest(),'cutoff':batch.cutoff.isoformat(),'previous':previous}
    if store.replace_pointer(latest,canonical(pointer),expected_generation):return PublicationResult('published',batch.batch_id)
    return PublicationResult('superseded',batch.batch_id)


def rollback(store,expected_generation,*,test_mode=False):
    latest=LATEST_TEST if test_mode else LATEST
    raw,generation=store.read(latest)
    if raw is None:raise ValueError('no publication to roll back')
    current=json.loads(raw);previous=current.get('previous')
    if previous is None:raise ValueError('no complete previous batch recorded')
    batch=PredictionBatch.model_validate_json(checked_read(store,{'key':previous['manifest_key'],'sha256':previous['sha256']}))
    validate_batch(batch,store,test_mode=test_mode)
    pointer={**previous,'schema_version':'outage-latest/v1','cutoff':batch.cutoff.isoformat(),
        'previous':{k:current[k] for k in ('batch_id','manifest_key','sha256')},'rollback':True}
    return store.replace_pointer(latest,canonical(pointer),expected_generation)


def consumer_ages(batch,now=None):
    now=time(now or datetime.now(timezone.utc))
    return {'source_run_age_hours':(now-batch.selected_run).total_seconds()/3600,
        'upstream_cutoff_age_hours':(now-batch.cutoff).total_seconds()/3600,
        'publication_age_hours':(now-batch.published_at).total_seconds()/3600,
        'stale':now<batch.cutoff or (now-batch.cutoff).total_seconds()>batch.stale_after_hours*3600}
