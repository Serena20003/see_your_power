"""Small native regression baseline for simulated allocation of authentic counts."""
from collections import defaultdict
from datetime import date
import hashlib,json,platform,time
import numpy as np
import lightgbm as lgb
from raster_pipeline.contracts import canonical,digest
from ..storage import put_bytes,checked_read
from .contracts import FEATURES,PREFIX,SEMANTICS,SamplingPolicy
from .dataset import load_dataset,ROLE_CODES,feature_matrix
from .sampling import sample_parent
from .history import load_geometry


def metrics(actual,predicted):
    a=np.asarray(actual,float);p=np.maximum(0,np.asarray(predicted,float))
    if not len(a):return {'rows':0,'eligible':False}
    return {'rows':len(a),'mae':float(np.mean(np.abs(a-p))),'rmse':float(np.sqrt(np.mean((a-p)**2))),
        'rmsle':float(np.sqrt(np.mean((np.log1p(a)-np.log1p(p))**2))),
        'mean_actual':float(a.mean()),'mean_predicted':float(p.mean()),
        'total_ratio_predicted_to_actual':float(p.sum()/a.sum()) if a.sum()>0 else None}


def _baseline(parents,parent_index,train_rows):
    selected=sorted(set(int(i) for i in parent_index[train_rows]));values=defaultdict(list)
    n=0
    for i in selected:
        p=parents[i];value=np.log1p(p['count'])
        q=(int(p['target_date'][5:7])-1)//3+1
        values[f"{p['county_fips']}:{q}:{p['horizon']}"].append(value)
        values[f"global:{q}:{p['horizon']}"].append(value);values['global'].append(value);n+=1
    return {'medians':{k:float(np.median(v)) for k,v in values.items()},'counts':{k:len(v) for k,v in values.items()},'minimum_group_parents':8,'training_parent_count':n}


def _baseline_parent_predictions(base,parents):
    output=[]
    for p in parents:
        q=(int(p['target_date'][5:7])-1)//3+1;keys=[f"{p['county_fips']}:{q}:{p['horizon']}",f"global:{q}:{p['horizon']}",'global']
        key=next(k for k in keys if base['counts'].get(k,0)>=base['minimum_group_parents'] or k=='global')
        output.append(float(np.expm1(base['medians'][key])))
    return np.asarray(output)


def train_model(store,dataset_ref,*,test_mode=False,rounds=200):
    if not test_mode and rounds!=200:raise ValueError('real v1 training uses pinned 200-round maximum')
    data,parents,dataset=load_dataset(store,dataset_ref,test_mode=test_mode);X=data['X'];y=data['y'];w=data['weights'];roles=data['role'];pidx=data['parent_index']
    train=np.where(roles==0)[0];tune=np.where(roles==1)[0]
    if len(train)<16 or len(tune)<16 or np.ptp(y[train])<=0:raise ValueError('insufficient variable real parent support')
    base=_baseline(parents,pidx,train);base_parent=_baseline_parent_predictions(base,parents)
    candidates=[];best=None;started=time.perf_counter()
    for depth,leaves in ((3,7),(5,15)):
        params={'objective':'regression_l1','metric':'l1','learning_rate':.05,'max_depth':depth,'num_leaves':leaves,
            'min_data_in_leaf':10 if test_mode else 200,'lambda_l2':5,'deterministic':True,'force_col_wise':True,
            'num_threads':4,'seed':42,'data_random_seed':42,'verbosity':-1}
        a=lgb.Dataset(X[train],label=y[train],weight=w[train],feature_name=list(FEATURES));b=lgb.Dataset(X[tune],label=y[tune],weight=w[tune],reference=a)
        model=lgb.train(params,a,num_boost_round=rounds,valid_sets=[b],callbacks=[lgb.early_stopping(20,verbose=False)])
        pred=model.predict(X[tune],num_threads=4);loss=float(np.average(np.abs(pred-y[tune]),weights=w[tune]))
        record={'parameters':params,'best_iteration':model.best_iteration,'tune_parent_weighted_log_mae':loss};candidates.append(record)
        if best is None or loss<best[0]:best=(loss,model,record)
    _,model,selected=best;text=model.model_to_string(num_iteration=model.best_iteration);native=lgb.Booster(model_str=text)
    log_prediction=native.predict(X,num_threads=4);prediction=np.maximum(0,np.expm1(log_prediction));true=np.expm1(y)
    county_prediction=np.bincount(pidx,weights=prediction,minlength=len(parents));report={}
    for name,code in ROLE_CODES.items():
        idx=np.where(roles==code)[0];pi=sorted(set(int(i) for i in pidx[idx]));actual=[parents[i]['count'] for i in pi]
        report[name]={'simulated_point_agreement':metrics(true[idx],prediction[idx]),
            'real_observed_county_aggregate':metrics(actual,county_prediction[pi]),
            'seasonal_county_baseline_aggregate':metrics(actual,base_parent[pi]),
            'group_disjoint_parent_count':len(pi),'county_count':len({parents[i]['county_fips'] for i in pi}),
            'interpretation':'Conditional observed-record impact errors; neither point-location accuracy nor occurrence probability.'}
    if not np.allclose(native.predict(X[:100],num_threads=1),model.predict(X[:100],num_threads=1),rtol=0,atol=1e-12):raise ValueError('native reload mismatch')
    sampling_sensitivity={}
    if not test_mode:
        geoms,_=load_geometry(store,dataset['geometry_ref']);test_parents=[p for p in parents if p['role']=='test']
        selected_parents=sorted(test_parents,key=lambda p:digest(p['parent_id']))[:2000]
        for seed in (7,99):
            altered=[];counts=[]
            policy=SamplingPolicy(**{**dataset['sampling_policy'],'seed':seed})
            for p in selected_parents:
                proxy=sample_parent(p,geoms[p['county_fips']],policy)
                features=feature_matrix(proxy['coordinates'],proxy['area_km2'],date.fromisoformat(p['target_date']),p['horizon'])
                altered.append(features);counts.append(p['count'])
            if altered:
                yy=np.maximum(0,np.expm1(native.predict(np.concatenate(altered),num_threads=4))).reshape(-1,policy.points_per_parent).sum(1)
                sampling_sensitivity[str(seed)]={'parent_count':len(counts),'county_aggregate':metrics(counts,yy),
                    'held_out_parent_ids_hash':digest([p['parent_id'] for p in selected_parents]),'model_refitted':False}
    importance=dict(zip(FEATURES,native.feature_importance(importance_type='gain').astype(float).tolist()))
    identity={'dataset_ref':dataset_ref,'native_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'features':list(FEATURES),'selected_trial':selected,'sampling_policy':dataset['sampling_policy'],'fixture':test_mode}
    model_id=digest(identity);prefix=f'{PREFIX}/models/{model_id}';native_raw=text.encode();key=prefix+'/model.txt';store.put(key,native_raw)
    native_ref={'key':key,'sha256':hashlib.sha256(native_raw).hexdigest(),'bytes':len(native_raw)}
    report_data={**SEMANTICS,'schema_version':'experimental-impact-evaluation/v1','model_id':model_id,'dataset_ref':dataset_ref,
        'fixture':test_mode,'metrics':report,'sampling_sensitivity':sampling_sensitivity,'trials':candidates,
        'feature_gain_association_only':importance,'training_seconds':time.perf_counter()-started,
        'test_selection':'test never used for candidate selection; tune selects log-MAE only',
        'dependence_caveat':'Parent groups are split-disjoint, not statistically independent; cross-county storms and ongoing impacts remain correlated. No event-independent confidence intervals are claimed.',
        'point_target_transform':'log1p(real observed county-day peak / points_per_parent)',
        'sample_weight':'1 / points_per_parent, so every real parent contributes total weight one',
        'weather_or_canopy_attribution':'Neither weather nor canopy was used. Spatial/calendar association only.',
        'aggregate_interpretation':'Sum predicted representative-point weights; comparison to reported county counts is meaningful only for available-record population.'}
    report_ref=put_bytes(store,prefix,canonical(report_data),'json')
    manifest={**SEMANTICS,'schema_version':'experimental-impact-model/v1','model_id':model_id,'fixture':test_mode,
        'identity':identity,'native':native_ref,'features':list(FEATURES),'dataset_ref':dataset_ref,'evaluation_ref':report_ref,
        'sampling_policy':dataset['sampling_policy'],'geometry_ref':dataset.get('geometry_ref'),
        'supported_county_fips':sorted(set(dataset['county_fips'])),'history_year':2018,'reference_training_boundary_vintage':2025,
        'environment':{'python':platform.python_version(),'lightgbm':lgb.__version__,'numpy':np.__version__},
        'training_feature_min':X[train].min(axis=0).tolist(),'training_feature_max':X[train].max(axis=0).tolist(),
        'baseline':base,'scope':'CONUS counties represented in the selected real 2018 source records and matching supplied 2025 polygons',
        'use_limit':'Experimental allocation baseline; no physical incident, household or new-unplanned-outage probability entitlement.'}
    model_ref=put_bytes(store,prefix,canonical(manifest),'json')
    return {'model_ref':model_ref,'evaluation_ref':report_ref,'model_id':model_id,'training_seconds':report_data['training_seconds']}


def load_model(store,model_ref,*,test_mode=False):
    meta=json.loads(checked_read(store,model_ref))
    if meta.get('schema_version')!='experimental-impact-model/v1' or tuple(meta.get('features',[]))!=FEATURES:raise ValueError('experimental model feature schema/order mismatch')
    if bool(meta['fixture'])!=test_mode:raise ValueError('fixture/live model mixing forbidden')
    if not meta.get('experimental') or not meta.get('locations_simulated') or meta.get('physical_outage_probability') is not False:raise ValueError('simulation semantics missing')
    raw=checked_read(store,meta['native'])
    if digest(meta['identity'])!=meta['model_id'] or hashlib.sha256(raw).hexdigest()!=meta['identity']['native_sha256']:raise ValueError('model identity mismatch')
    model=lgb.Booster(model_str=raw.decode())
    if model.feature_name()!=list(FEATURES):raise ValueError('native feature order mismatch')
    return model,meta
