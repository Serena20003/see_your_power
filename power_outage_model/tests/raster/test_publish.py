from datetime import timedelta
import json
import numpy as np
import pytest
from raster_pipeline.storage import LocalStore
from raster_pipeline.publish import publish_batch
from raster_pipeline.rasters import write_weather_cog,validate_cog
from raster_pipeline.contracts import WEATHER_BANDS,WEATHER_UNITS
from test_windows import G,T
from raster_pipeline.windows import target_intervals


def test_cog_roundtrip_and_units(tmp_path):
    path=tmp_path/'w.tif';a=np.array([[[2]],[[10]],[[5]]],dtype='float32')
    write_weather_cog(path,a,G,target_intervals(T)[0])
    actual,tags=validate_cog(path,G,WEATHER_BANDS,WEATHER_UNITS)
    np.testing.assert_equal(actual,a)
    assert tags['temperature_c_temporal_semantics']=='endpoint_sample'


def test_store_immutability_and_conditional_pointer(tmp_path):
    s=LocalStore(tmp_path)
    s.put('x',b'a');s.put('x',b'a')
    with pytest.raises(ValueError):s.put('x',b'b')
    assert s.replace_pointer('latest.json',b'a',None)
    assert not s.replace_pointer('latest.json',b'b',None)
    value,generation=s.read('latest.json')
    assert s.replace_pointer('latest.json',b'b',generation)
    for key in ['../outside','/absolute','a/../../outside']:
        with pytest.raises(ValueError):s.put(key,b'x')


def test_complete_publish_idempotency_crash_and_races(tmp_path):
    from raster_pipeline.run import run_fixture
    s=LocalStore(tmp_path)
    m=run_fixture(T,s)
    pointer,generation=s.read('raster/v1/latest.json')
    assert len(m['intervals'])==72
    assert len(list(tmp_path.glob('raster/v1/runs/*/weather/*.tif')))==72
    assert len(list(tmp_path.glob('raster/v1/runs/*/risk/*.tif')))==72
    assert publish_batch(m,s,generation).status=='already_published'
    old=json.loads(pointer)
    # A crash writing an unrelated incomplete run cannot move the pointer.
    broken={**m,'batch_id':'broken','intervals':m['intervals'][:-1]}
    with pytest.raises(ValueError):publish_batch(broken,s,generation)
    assert s.read('raster/v1/latest.json')[0]==pointer
    newer=run_fixture(T+timedelta(hours=1),s)
    assert publish_batch(m,s,generation).status=='superseded'
    assert json.loads(s.read('raster/v1/latest.json')[0])['batch_id']==newer['batch_id']

@pytest.mark.parametrize('damage',['duplicate','nonfinite','out_of_range','partial','geometry','checksum'])
def test_publication_rejects_corrupt_data(tmp_path,damage):
    from raster_pipeline.run import run_fixture
    from raster_pipeline.contracts import canonical
    from raster_pipeline.download import file_sha
    import rasterio
    s=LocalStore(tmp_path)
    m=run_fixture(T,s);pointer,generation=s.read('raster/v1/latest.json')
    # Deliberately corrupt staged objects to exercise independent verification.
    m=json.loads(json.dumps(m));m['batch_id']='bad-'+damage
    if damage=='duplicate':m['intervals'][1]=m['intervals'][0]
    elif damage=='checksum':m['intervals'][0]['weather']['sha256']='0'*64
    else:
        key=m['intervals'][0]['risk']['key'];path=tmp_path/key
        with rasterio.open(path,'r+',IGNORE_COG_LAYOUT_BREAK='YES') as out:
            a=out.read()
            if damage=='nonfinite':a[0,0,0]=np.nan
            elif damage=='out_of_range':a[0,0,0]=1.1
            elif damage=='partial':a[0,0,0]=-9999
            elif damage=='geometry':out.transform=rasterio.Affine(2,0,-120,0,-1,35)
            if damage!='geometry':out.write(a)
        m['intervals'][0]['risk']['sha256']=file_sha(path)
    with pytest.raises(ValueError):publish_batch(m,s,generation)
    assert s.read('raster/v1/latest.json')[0]==pointer


def test_default_grid_cog_preserves_north_up_pixels_and_has_overviews(tmp_path):
    import rasterio
    from raster_pipeline.contracts import GridSpec
    grid=GridSpec();values=np.zeros((3,*grid.shape),dtype='float32')
    values[:,0,0]=[1,2,3];values[:,-1,-1]=[4,5,6]
    path=tmp_path/'conus.tif';write_weather_cog(path,values,grid,target_intervals(T)[0])
    actual,_=validate_cog(path,grid,WEATHER_BANDS,WEATHER_UNITS)
    np.testing.assert_equal(actual[:,0,0],[1,2,3]);np.testing.assert_equal(actual[:,-1,-1],[4,5,6])
    with rasterio.open(path) as src:
        assert src.overviews(1)
        assert src.xy(0,0)==(-124.9375,49.9375)
