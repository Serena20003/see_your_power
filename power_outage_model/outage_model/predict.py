"""Three-window scoring. Unsupported cells get a separate maximum hourly score."""
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import tempfile
import numpy as np
from raster_pipeline.contracts import NODATA,canonical,digest
from raster_pipeline.rasters import write_cog
from .contracts import HORIZONS, PredictionBatch, FeatureRow, time
from .features import build_features
from .train import raw_margins
from .calibration import calibrated_array
from .support import assess_support
from .artifacts import validate_bundle,save_bundle
from .splits import feature_bytes
from .storage import put_bytes
from .explain import explain


def approved_scopes(bundle):
    e=bundle.evidence
    if e is None or e.role!='test':return []
    return [s for s in e.scopes if s.get('passed')]


def predict_batch(archive,bundle,policy):
    store=policy['store'];test_mode=policy.get('test_mode',False)
    validate_bundle(bundle,test_mode=test_mode)
    if archive.ref.fixture!=test_mode:raise ValueError('fixture/live inference mixing forbidden')
    if archive.grid.grid_id!=bundle.grid_id or archive.manifest['static_version']!=bundle.static_version or str(archive.manifest.get('canopy_vintage'))!=bundle.canopy_vintage:
        raise ValueError('model grid/static-vintage incompatibility')
    now=time(policy.get('now') or datetime.now(timezone.utc));cutoff=time(archive.manifest['cutoff'])
    age=policy.get('stale_after_hours',2)
    if age!=2 and not test_mode:raise ValueError('live stale age must match pinned policy')
    model_ref=save_bundle(bundle,store)
    stale=(now-cutoff).total_seconds()>age*3600 or cutoff>now
    bid=digest({'archive':archive.ref.model_dump(mode='json'),'model':model_ref.model_dump(mode='json'),'stale':stale,'stale_after_hours':age,'schema':'outage-score/v1'})
    prefix=f'outage/v1/{"testing/" if test_mode else ""}runs/{bid}'
    previous,_=store.read(prefix+'/manifest.json')
    if previous:
        from .publish import validate_batch
        batch=PredictionBatch.model_validate_json(previous);validate_batch(batch,store,test_mode=test_mode);return batch
    features=build_features(archive)
    support=assess_support(features,bundle.support,approved_scopes(bundle),cutoff,now=now,stale_after_hours=age)
    margins=raw_margins(bundle,features)
    feature_ref=put_bytes(store,prefix,feature_bytes(features),'parquet')
    windows=[];explanations=[];grid=archive.grid
    with tempfile.TemporaryDirectory() as tmp:
        for h,horizon in enumerate(HORIZONS):
            idx=np.asarray([i for i,k in enumerate(features.keys) if k.horizon_id==horizon],int)
            status=support['status'][idx].reshape(grid.shape)
            probability=np.full(grid.shape,NODATA,dtype='float32');supported=status.reshape(-1)==1
            if supported.any():
                p=calibrated_array(bundle.calibrations[horizon],margins[idx[supported]],[support['region_ids'][i] for i in idx[supported]])
                probability.reshape(-1)[supported]=p
            scores=archive.fallback[h*24:(h+1)*24]
            valid=np.isfinite(scores).all(0)&(scores!=NODATA).all(0)&archive.static.analysis_mask
            fallback=np.where(valid & (not stale),scores.max(0),NODATA).astype('float32')
            # Probability eligibility cannot bypass an incomplete upstream score
            # dependency; weather integrity is independently validated on archive load.
            if np.any((status==1)&~valid):raise ValueError('incomplete fallback coverage')
            begin=time(archive.manifest['target_start'])+timedelta(hours=h*24)
            entries={'horizon_id':horizon,'start':begin.isoformat(),'end':(begin+timedelta(hours=24)).isoformat(),
                'status_counts':{str(s):int(np.sum(status==s)) for s in range(6)},
                'scope_ids':sorted({support['scope_ids'][i] for i in idx if support['scope_ids'][i] is not None})}
            for name,array,band,unit,coverage in (
                ('probability',probability,'outage_probability','1',False),
                ('status',status,'probability_status','code',True),
                ('fallback',fallback,'peak_hourly_weather_risk_score','1',False)):
                path=Path(tmp)/(name+'.tif')
                info=write_cog(path,array,grid,[band],[unit],coverage=coverage,tags={
                    'interval_start':entries['start'],'interval_end':entries['end'],
                    'value_kind':'probability' if name=='probability' else ('heuristic_score' if name=='fallback' else 'status'),
                    'model_id':bundle.model_id,'upstream_cutoff':cutoff.isoformat()})
                key=f'{prefix}/{horizon}-{name}.tif';store.put(key,path.read_bytes());entries[name]={**info,'key':key}
            windows.append(entries)
            # Persist one reconstructible representative per horizon, plus Parquet
            # for deterministic on-demand full contributions for every other row.
            examples=[i for i in idx if features.eligible[i]]
            if examples:
                i=next((i for i in examples if support['status'][i]==1),examples[0])
                row=FeatureRow(key=features.keys[i],names=features.names,values=tuple(features.values[i]),
                    region=support['region_ids'][i],provenance=features.provenance,status=support['reasons'][i])
                record=explain(bundle,row)
                explanations.append(put_bytes(store,prefix,canonical(record),'json'))
    prediction=PredictionBatch(batch_id=bid,archive=archive.ref,model=model_ref,cutoff=cutoff,
        target_start=archive.manifest['target_start'],selected_run=archive.manifest['selected_run'],published_at=now,
        grid=grid,windows=tuple(windows),features=feature_ref,explanations=tuple(explanations),fixture=test_mode,
        stale_after_hours=age,fallback_recipe={'label':'Peak hourly weather risk score in this 24-hour window',
            'aggregation':'max of exactly 24 hourly environmental scores; all contributing land values valid',
            'upstream_method':archive.manifest['risk_config'],'upstream_config_hash':archive.manifest['risk_config_hash'],
            'interpretation':'uncalibrated environmental score; not an outage probability',
            'consumer_age_rule':'recompute now - cutoff at read time; stale after 2 hours',
            'explanation_recipe':'load pinned model and feature Parquet; select exact RowKey; call explain'})
    # Persist complete manifest only after every dependency exists.
    store.put(prefix+'/manifest.json',canonical(prediction))
    return prediction
