"""Split authentic county/date parents before allocating any proxy locations."""
from collections import Counter
from datetime import date,timedelta
import io,json,math,sys
import numpy as np
from raster_pipeline.contracts import canonical,digest
from ..storage import checked_read,put_bytes
from .contracts import SamplingPolicy,FEATURES,PREFIX,SEMANTICS,check_headroom,ROOT
from .history import load_days,load_geometry
from .sampling import sample_parent,stable_seed

ROLE_CODES={'train':0,'tune':1,'test':2,'county_holdout':3}
BOUNDARIES={'train':(date(2018,1,1),date(2018,9,1)),
            'tune':(date(2018,9,4),date(2018,11,1)),
            'test':(date(2018,11,4),date(2019,1,1))}


def role_for_anchor(anchor):
    for role,(start,end) in BOUNDARIES.items():
        if start<=anchor and anchor+timedelta(days=3)<=end:return role
    return None


def make_parents(days,geometries,*,year=2018,max_parents=180000):
    if not 1<=max_parents<=180000:raise ValueError('parent cap must be between1 and180000 for the bounded v1 CPU job')
    if year!=2018:raise ValueError('v1 exact chronological policy supports 2018; revise boundaries for another year')
    lookup={(d.county_fips,d.day):d for d in days};parents=[];stats=Counter()
    counties=sorted({d.county_fips for d in days});anchor=date(year,1,1)
    while anchor.year==year:
        role=role_for_anchor(anchor)
        if role is None:anchor+=timedelta(days=3);continue
        for fips in counties:
            if fips not in geometries:stats['county_anchor_without_geometry']+=1;continue
            county_held=stable_seed(f'county-holdout:{fips}',42)%10==0
            if county_held and role!='test':stats['county_holdout_fit_anchors_excluded']+=1;continue
            assigned='county_holdout' if county_held else role
            for h in range(3):
                day=anchor+timedelta(days=h);observation=lookup.get((fips,day))
                if observation is None:stats['missing_county_day_excluded']+=1;continue
                pid=f'{fips}:{anchor.isoformat()}:{h}'
                parents.append({'parent_id':pid,'group_id':f'{fips}:{anchor.isoformat()}',
                    'county_fips':fips,'anchor_date':anchor.isoformat(),'target_date':day.isoformat(),
                    'horizon':h,'count':observation.reported_peak_customers,'received_records':observation.received_records,
                    'role':assigned})
                stats['parent_'+assigned]+=1
        anchor+=timedelta(days=3)
    # Uniform parent subsampling is deterministic, independent of count and
    # retains every role/county where feasible. Sampling cap is explicit.
    original=len(parents)
    if original>max_parents:
        parents=sorted(parents,key=lambda p:stable_seed('parent-cap:'+p['parent_id'],42))[:max_parents]
        parents.sort(key=lambda p:(p['anchor_date'],p['county_fips'],p['horizon']))
    stats.update({'eligible_parents_before_cap':original,'selected_parents':len(parents)})
    return parents,dict(stats)


def feature_matrix(coordinates,area,anchor,horizon):
    phase=2*np.pi*((anchor-date(anchor.year,1,1)).days+.5)/(date(anchor.year+1,1,1)-date(anchor.year,1,1)).days
    weekday=2*np.pi*anchor.weekday()/7
    return np.column_stack((coordinates[:,0],coordinates[:,1],
        np.full(len(coordinates),np.sin(phase)),np.full(len(coordinates),np.cos(phase)),
        np.full(len(coordinates),np.sin(weekday)),np.full(len(coordinates),np.cos(weekday)),
        np.full(len(coordinates),np.log1p(area)),np.full(len(coordinates),horizon))).astype('float32')


def build_dataset(store,history_ref,geometry_ref,*,policy=SamplingPolicy(),max_parents=180000,root=ROOT):
    check_headroom(root,350_000_000)
    days,history=load_days(store,history_ref);geometries,geo_meta=load_geometry(store,geometry_ref)
    parents,stats=make_parents(days,geometries,year=history['year'],max_parents=max_parents)
    if not parents:raise ValueError('no real county parent observations with usable geometry')
    X=np.empty((len(parents)*policy.points_per_parent,len(FEATURES)),dtype='float32')
    y=np.empty(len(X),dtype='float32');weights=np.empty(len(X),dtype='float32');role=np.empty(len(X),dtype='uint8');parent_index=np.empty(len(X),dtype='int32')
    for i,p in enumerate(parents):
        if i%20000==0:print(f'sampling real parent {i:,}/{len(parents):,}',file=sys.stderr,flush=True)
        proxy=sample_parent(p,geometries[p['county_fips']],policy);a=i*policy.points_per_parent;b=a+policy.points_per_parent
        X[a:b]=feature_matrix(proxy['coordinates'],proxy['area_km2'],date.fromisoformat(p['target_date']),p['horizon'])
        y[a:b]=np.log1p(proxy['allocated_customers']);weights[a:b]=proxy['training_weights'];role[a:b]=ROLE_CODES[p['role']];parent_index[a:b]=i
    # Every parent's proxies inherit the already assigned parent split.
    for name,code in ROLE_CODES.items():
        idx=role==code
        if idx.sum()<16:raise ValueError('insufficient real county parent support for '+name)
    buffer=io.BytesIO();np.savez_compressed(buffer,X=X,y=y,weights=weights,role=role,parent_index=parent_index)
    matrix_ref=put_bytes(store,f'{PREFIX}/datasets',buffer.getvalue(),'npz')
    parent_ref=put_bytes(store,f'{PREFIX}/datasets',canonical(parents),'json')
    manifest={**SEMANTICS,'schema_version':'experimental-random-point-dataset/v1','fixture':history['fixture'],
        'history_ref':history_ref,'geometry_ref':geometry_ref,'geometry':geo_meta,'sampling_policy':policy.model_dump(),
        'features':list(FEATURES),'matrix':matrix_ref,'parents':parent_ref,'statistics':stats,
        'rows':len(X),'parent_count':len(parents),'counties':len({p['county_fips'] for p in parents}),
        'county_fips':sorted({p['county_fips'] for p in parents}),
        'split_policy':{'boundaries':{k:[str(t) for t in v] for k,v in BOUNDARIES.items()},'issue_cadence_days':3,
            'target_windows_hours':[[0,24],[24,48],[48,72]],'county_holdout':'stable SHA256 seed42 modulo10 ==0',
            'parent_cap':max_parents,'cap_selection':'uniform deterministic hash independent of impact; every proxy stays with its parent'},
        'weather_features_used':False,'canopy_features_used':False,
        'observation_condition':'conditional on county-day having at least one valid reported source count; omissions remain unknown',
        'count_aggregation':history['daily_target'],'source_coverage_ref':history['coverage_metadata']}
    return put_bytes(store,f'{PREFIX}/datasets',canonical(manifest),'json')


def load_dataset(store,ref,*,test_mode=False):
    meta=json.loads(checked_read(store,ref))
    if meta.get('schema_version')!='experimental-random-point-dataset/v1' or tuple(meta['features'])!=FEATURES:raise ValueError('experimental dataset schema/order mismatch')
    if bool(meta['fixture'])!=test_mode:raise ValueError('fixture/live experimental dataset mixing forbidden')
    with np.load(io.BytesIO(checked_read(store,meta['matrix'])),allow_pickle=False) as arr:data={k:arr[k] for k in arr.files}
    parents=json.loads(checked_read(store,meta['parents']))
    if len(data['X'])!=meta['rows'] or data['X'].shape[1]!=len(FEATURES) or not np.isfinite(data['X']).all():raise ValueError('invalid experimental matrix')
    n=len(data['X'])
    if any(data[k].shape!=(n,) for k in ('y','weights','role','parent_index')) or not np.isfinite(data['y']).all() or not np.isfinite(data['weights']).all() or np.any(data['weights']<=0) or np.any(data['y']<0):
        raise ValueError('invalid experimental target/weight dimensions or values')
    pi=data['parent_index']
    if not np.issubdtype(pi.dtype,np.integer) or np.any(pi<0) or np.any(pi>=len(parents)):raise ValueError('invalid parent index')
    if not np.isin(data['role'],list(ROLE_CODES.values())).all():raise ValueError('invalid parent role code')
    parent_roles=np.asarray([ROLE_CODES[p['role']] for p in parents])
    if not np.array_equal(data['role'],parent_roles[pi]):raise ValueError('proxy split differs from parent assignment')
    parent_weights=np.bincount(pi,weights=data['weights'],minlength=len(parents))
    if not np.allclose(parent_weights,1.,atol=1e-6,rtol=0):raise ValueError('parent training weight must sum to one')
    allocated_mass=np.bincount(pi,weights=np.expm1(data['y'].astype(float)),minlength=len(parents))
    if not np.allclose(allocated_mass,[p['count'] for p in parents],atol=1e-5,rtol=3e-6):raise ValueError('simulated allocation does not conserve real parent count')
    return data,parents,meta
