import copy
from datetime import datetime,timezone
import numpy as np
import pytest
import rasterio
from raster_pipeline.contracts import GridSpec,NODATA
from raster_pipeline.fixtures import generate_fixture,load_fixture_config
from raster_pipeline.run import fixture_static,run,_static,recalculate
from raster_pipeline.static import load_static,save_static,supported_land
from raster_pipeline.publish import validate_batch
from raster_pipeline.rasters import write_cog
from raster_pipeline.risk import RiskConfig
from raster_pipeline.storage import LocalStore


def test_static_gaps_require_opt_in_and_remain_nodata_through_replay(tmp_path):
    grid=GridSpec(grid_id='coverage-test',west=-120,south=34,east=-118,north=35,resolution=1)
    cutoff=datetime(2026,11,16,6,tzinfo=timezone.utc);raw=tmp_path/'raw'
    generate_fixture(raw,cutoff,grid)
    store=LocalStore(tmp_path/'objects')
    manifest=fixture_static(grid,store);context=load_static(manifest,grid,store)
    context.version+='-gaps';context.valid_land_fraction[0,1]=.5;context.coverage[1,0,1]=2
    static=save_static(context,manifest.sources,manifest.recipe,store,fixture=True)
    cfg=load_fixture_config(raw).model_copy(update={'static_manifest':f'raster/v1/static/{static.static_version}/manifest.json'})
    with pytest.raises(ValueError,match='insufficient canopy'):_static(cfg,store,True)
    cfg=cfg.model_copy(update={'allow_static_gaps':True})
    m=run(cfg,cutoff,store,fixture_dir=raw,work_dir=tmp_path/'work')
    assert m['coverage_policy']=='supported_land'
    assert m['coverage_summary']=={'analysis_land_cells':2,'scored_land_cells':1,
        'missing_environment_cells':1,'minimum_valid_land_fraction':.95}
    validate_batch(m,store)
    with pytest.raises(ValueError,match='insufficient canopy'):
        validate_batch({**m,'coverage_policy':'complete_land'},store)
    incorrect=copy.deepcopy(m);incorrect['coverage_summary']['missing_environment_cells']=0
    with pytest.raises(ValueError,match='coverage summary'):validate_batch(incorrect,store)

    replay=recalculate(m,store,RiskConfig(canopy_weight=.2))
    assert replay['coverage_policy']=='supported_land'
    assert replay['coverage_summary']==m['coverage_summary']
    assert replay['weather_recipe']==m['weather_recipe']
    obj=replay['intervals'][0]['risk'];path=store.path(obj['key'])
    with rasterio.open(path) as src:
        a=src.read();tags=src.tags()
    assert a[0,0,1]==NODATA and 0<=a[0,0,0]<=1
    # A valid COG with a fabricated score in the unsupported cell is rejected,
    # even if the attacker updates its checksum and valid-cell count.
    a[0,0,1]=0
    info=write_cog(path,a,grid,['risk_score'],['1'],tags={k:v for k,v in tags.items() if k!='grid_id'})
    obj.update(info)
    with pytest.raises(ValueError,match='coverage'):validate_batch(replay,store)
    context.coverage[1]=2
    with pytest.raises(ValueError,match='no land cells'):supported_land(context,allow_gaps=True)
