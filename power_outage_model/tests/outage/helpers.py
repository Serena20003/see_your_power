"""Small generated data ONLY for unit/integration tests, never runtime fallback."""
from datetime import datetime,timezone,timedelta
import numpy as np
from outage_model.contracts import *
from outage_model.features import load_registry
from raster_pipeline.contracts import digest

T=datetime(2020,1,1,tzinfo=timezone.utc)

def dataset(n=120,*,fixture=True):
    names=tuple(f['name'] for f in load_registry()['features'])
    keys=[]; metadata=[]; rows=[]; labels=[]; matrices=[]
    rng=np.random.default_rng(42)
    boundaries={}
    for rid,role in enumerate(ROLES):
        start=T+timedelta(days=rid*150); end=start+timedelta(days=140)
        boundaries[role]=(start,end)
        for h in HORIZONS:
            for i in range(n):
                # Four days apart, independent weekly storm IDs across cells.
                stamp=start+timedelta(days=4+i%120)
                key=RowKey(archive_id=f'{role}-{i}',grid_id='fixture',row=i,col=0,horizon_id=h)
                x=rng.normal(size=len(names)); y=int(x[0]+rng.normal(scale=.3)>0)
                keys.append(key);matrices.append(x);labels.append(y)
                meta={'cutoff':stamp.isoformat(),'start':stamp.isoformat(),'end':(stamp+timedelta(hours=24)).isoformat(),
                      'season':f'Q{(stamp.month-1)//3+1}','analysis_land':True,'retrospective_only':False}
                metadata.append(meta)
                rows.append({**meta,'domain_id':f'u{i%3}','state':'CA' if i%2 else 'OR','region':'West' if i%2 else 'Northwest',
                    'block_id':f'{role}-week-{i//7}','event_ids':(f'{role}-event-{i}',) if y else (),
                    'coverage_ids':('audited',),'freeze_time':(T+timedelta(days=1000)).isoformat()})
    values=np.array(matrices,dtype='float32')
    f=FeatureTable(names=names,values=values,keys=tuple(keys),eligible=np.ones(len(keys),bool),metadata=tuple(metadata),
        fixture=fixture,provenance={'registry_hash':digest(load_registry()),'static_version':'test-static','canopy_vintage':'test-only',
          'normalization_reproducible':True})
    d=DatasetSnapshot(dataset_id=digest({'fixture':True,'rows':len(keys)}),features=f,labels=np.array(labels),rows=tuple(rows),
        exclusions=(),freeze_time=T+timedelta(days=1000),lineage={'verified_labels':True,'event_blocks_reviewed':True,'fixture':fixture})
    policy=SplitPolicy(version='unit-test-only',boundaries=boundaries,reviewed_blocks_hash='test-only')
    return d,policy
