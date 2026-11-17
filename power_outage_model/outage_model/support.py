"""Training-only robust nearest-reference screen; scope approval is independent."""
from datetime import datetime,timezone
import numpy as np
from sklearn.neighbors import NearestNeighbors
from .contracts import HORIZONS, DataReadinessError, time
from raster_pipeline.contracts import digest


def _nearest(reference,values):
    return NearestNeighbors(n_neighbors=1,n_jobs=1).fit(reference).kneighbors(values,return_distance=True)[0][:,0]


def fit_support(train_features,train_blocks,config):
    X=train_features.values;blocks=np.asarray(train_blocks)
    if len(blocks)!=len(X) or not train_features.eligible.all():raise ValueError('eligible training blocks required')
    result={'schema_version':'outage-support/v1','feature_names':list(train_features.names),'horizons':{},
        'distance':'Euclidean after training median/IQR scaling','percentile':99,'seed':42}
    for h in HORIZONS:
        idx=np.asarray([i for i,k in enumerate(train_features.keys) if k.horizon_id==h],int)
        if not len(idx) or len(set(blocks[idx]))<2:raise DataReadinessError('support requires multiple independent training blocks per horizon')
        A=np.asarray(X[idx],float);b=blocks[idx]
        median=np.median(A,axis=0);q1,q3=np.quantile(A,[.25,.75],axis=0);scale=np.where(q3>q1,q3-q1,1.)
        z=(A-median)/scale;rng=np.random.default_rng(42)
        sample=np.sort(rng.choice(len(A),min(config.get('max_reference',10000),len(A)),replace=False))
        reference=z[sample];ref_blocks=b[sample];dist=np.empty(len(A))
        for block in sorted(set(b)):
            other=reference[ref_blocks!=block]
            if not len(other):raise DataReadinessError('reference sample lacks other-block neighbors')
            mask=b==block;dist[mask]=_nearest(other,z[mask])
        result['horizons'][h]={'median':median.tolist(),'scale':scale.tolist(),'min':A.min(0).tolist(),'max':A.max(0).tolist(),
            'reference':reference.tolist(),'reference_row_ids':[train_features.keys[idx[j]].id for j in sample],
            'reference_blocks':ref_blocks.tolist(),'distance_cutoff':float(np.quantile(dist,.99)),
            'training_block_count':len(set(b)),'training_row_hash':digest([train_features.keys[i].id for i in idx])}
    return result


def assess_support(features,bundle,scopes,cutoff,*,now=None,stale_after_hours=2):
    if list(features.names)!=bundle['feature_names']:raise ValueError('support feature schema/order mismatch')
    now=time(now or datetime.now(timezone.utc));cutoff=time(cutoff)
    stale=(now-cutoff).total_seconds()>stale_after_hours*3600 or cutoff>now
    n=len(features.keys);status=np.full(n,2,dtype='uint8');distance=np.full(n,np.nan);regions=['unknown']*n;scope_ids=[None]*n;reasons=['insufficient_validation']*n
    # Explicit fixed-cell scope; a state or region name alone never grants coverage.
    scope_lookup={}
    for scope in scopes:
        for row,col in scope.get('cells',[]):
            key=(scope['grid_id'],int(row),int(col),scope['horizon_id'],scope['season'])
            if key in scope_lookup and scope_lookup[key]['scope_id']!=scope['scope_id']:raise ValueError('overlapping approved scopes')
            scope_lookup[key]=scope
    for h in HORIZONS:
        idx=np.asarray([i for i,k in enumerate(features.keys) if k.horizon_id==h and features.eligible[i]],int)
        ref=bundle['horizons'][h]
        if len(idx):distance[idx]=_nearest(np.asarray(ref['reference']),
            (features.values[idx]-np.asarray(ref['median']))/np.asarray(ref['scale']))
    for i,(key,meta) in enumerate(zip(features.keys,features.metadata)):
        if not meta.get('analysis_land',True):status[i]=0;reasons[i]='outside_domain';continue
        if stale:status[i]=5;reasons[i]='stale_forecast';continue
        if not features.eligible[i]:status[i]=4;reasons[i]='invalid_inputs';continue
        scope=scope_lookup.get((key.grid_id,key.row,key.col,key.horizon_id,meta['season']))
        if scope is None:continue
        regions[i]=scope.get('region','unknown');scope_ids[i]=scope['scope_id']
        ref=bundle['horizons'][key.horizon_id];values=features.values[i]
        if np.any(values<np.asarray(ref['min'])) or np.any(values>np.asarray(ref['max'])):
            status[i]=3;reasons[i]='outside_training_range'
        elif not np.isfinite(distance[i]) or distance[i]>ref['distance_cutoff']:
            status[i]=3;reasons[i]='outside_reference_distance'
        else:status[i]=1;reasons[i]='validated_local_probability'
    return {'status':status,'distance':distance,'region_ids':regions,'scope_ids':scope_ids,'reasons':reasons}
