import numpy as np
from outage_model.audits import grouped_permutation, route_regions
from outage_model.features import load_registry
from raster_pipeline.contracts import digest


def test_region_center_routing_and_boundary_flag():
    from raster_pipeline.contracts import GridSpec
    grid=GridSpec(grid_id='test',west=-120,east=-118,south=34,north=35,resolution=1)
    states={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'STUSPS':'CA'},
        'geometry':{'type':'Polygon','coordinates':[[[-120,34],[-118.5,34],[-118.5,35],[-120,35],[-120,34]]]}}]}
    result=route_regions(grid,states,expected_sha256=digest(states))
    assert result['0,0']['region']=='West'
    assert result['0,1']['boundary']


def test_grouped_permutation_excludes_metadata_and_uses_blocks():
    rng=np.random.default_rng(42);x=rng.normal(size=(60,17));y=(x[:,0]>0).astype(int)
    names=tuple(f['name'] for f in load_registry()['features'])
    result=grouped_permutation(lambda a:1/(1+np.exp(-a[:,0])),x,y,names,[str(i//10) for i in range(60)],repeats=3)
    assert result['wind']['mean_brier_increase']>0
    assert result['canopy']['mean_brier_increase']==0


def test_generated_audits_are_bound_and_failed_holdouts_remain_failed(tmp_path):
    from .test_explain import model_bundle
    from .helpers import dataset
    from outage_model.splits import make_splits
    from outage_model.audits import generate_audits
    from outage_model.evaluate import validate_audit_reports
    from raster_pipeline.storage import LocalStore
    from outage_model.storage import checked_read
    import json
    import pytest
    d,b=model_bundle();_,policy=dataset(40);s=make_splits(d,policy);store=LocalStore(tmp_path)
    refs=generate_audits(b,d,s,store,[])
    reports={k:json.loads(checked_read(store,v)) for k,v in refs.items()}
    assert reports['explanation_audit']['passed']
    assert reports['grouped_importance']['results']['0_24']['response_curves']
    assert not reports['geographic_holdouts']['passed']
    with pytest.raises(ValueError,match='unsuccessful'):validate_audit_reports(reports,b,d,s)
