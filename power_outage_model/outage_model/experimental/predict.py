"""Inference without fitting, with experimental allocation semantics in every result."""
from datetime import date,timedelta
import numpy as np
from .contracts import FEATURES,SEMANTICS,SamplingPolicy
from .train import load_model
from .history import load_geometry
from .sampling import sample_parent
from .dataset import feature_matrix


def predict_points(store,model_ref,values,feature_names,*,test_mode=False):
    if tuple(feature_names)!=FEATURES:raise ValueError('inference feature order mismatch')
    X=np.asarray(values,dtype='float32')
    if X.ndim!=2 or X.shape[1]!=len(FEATURES) or not np.isfinite(X).all():raise ValueError('invalid inference inputs')
    model,meta=load_model(store,model_ref,test_mode=test_mode)
    prediction=np.maximum(0,np.expm1(model.predict(X,num_threads=4)))
    supported=((X>=np.asarray(meta['training_feature_min']))&(X<=np.asarray(meta['training_feature_max']))).all(axis=1)
    return {**SEMANTICS,'model_id':meta['model_id'],'within_training_feature_ranges':supported.tolist(),'feature_names':list(FEATURES),
        'allocated_customer_impact':prediction.tolist(),'fixture':test_mode,
        'applicability':'conditional simulated allocation of observed-record county impact; not calibrated occurrence probability'}


def predict_counties(store,model_ref,anchor_date,county_fips,*,test_mode=False):
    model,meta=load_model(store,model_ref,test_mode=test_mode);geometries,geo_meta=load_geometry(store,meta['geometry_ref'])
    policy=SamplingPolicy(**meta['sampling_policy']);anchor=date.fromisoformat(anchor_date);records=[];unsupported=[]
    supported=set(meta['supported_county_fips'])
    for fips in sorted(set(county_fips)):
        if fips not in supported or fips not in geometries:
            unsupported.append(fips);continue
        for h in range(3):
            parent={'parent_id':f'{fips}:{anchor.isoformat()}:{h}','county_fips':fips,'anchor_date':anchor.isoformat(),
                    'horizon':h,'count':0.,'role':'inference'}
            proxies=sample_parent(parent,geometries[fips],policy);day=anchor+timedelta(days=h)
            X=feature_matrix(proxies['coordinates'],proxies['area_km2'],day,h)
            values=np.maximum(0,np.expm1(model.predict(X,num_threads=4)))
            records.append({'county_fips':fips,'target_date':day.isoformat(),'horizon_hours':[24*h,24*(h+1)],
                'window_start_utc':day.isoformat()+'T00:00:00Z','window_end_utc':(day+timedelta(days=1)).isoformat()+'T00:00:00Z',
                'allocated_county_customer_impact':float(values.sum()),
                'within_training_feature_ranges':bool(((X>=np.asarray(meta['training_feature_min']))&(X<=np.asarray(meta['training_feature_max']))).all()),
                'points':[{'longitude':float(x),'latitude':float(y),'allocated_customer_impact':float(v)} for (x,y),v in zip(proxies['coordinates'],values)],
                'locations_simulated':True,'experimental':True})
    return {**SEMANTICS,'model_id':meta['model_id'],'anchor_date':anchor.isoformat(),'anchor_time_utc':anchor.isoformat()+'T00:00:00Z','windows_are_fixed_utc_days':True,'records':records,
        'unsupported_counties':unsupported,'coverage':{'modeled_counties':len(supported),'geometry_counties':len(geometries),'source_unsupported_county_fips':sorted(set(geometries)-supported),'scope':'CONUS source-record-represented counties; not nationwide validation'},'geography':geo_meta,'calendar_extrapolation':anchor.year!=meta['history_year'],
        'use_limit':meta['use_limit'],'probability_release_approved':False}
