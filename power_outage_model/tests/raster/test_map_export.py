import json
import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from raster_pipeline.contracts import GridSpec, NODATA
from raster_pipeline.rasters import write_cog
from raster_pipeline.storage import LocalStore
from raster_pipeline.map_export import colorize, weather_window, publish_weather_map


def test_colorize_preserves_zero_and_masks_nodata():
    rgba=colorize(np.array([[0,50,100,255,np.nan]]), 'canopy')
    assert rgba.shape==(4,1,5)
    assert rgba[3,0,0]>0
    assert rgba[3,0,3:].tolist()==[0,0]
    assert not np.array_equal(rgba[:3,0,0],rgba[:3,0,2])


def manifest_and_store(tmp_path):
    store=LocalStore(tmp_path/'science');g=GridSpec(grid_id='map-test',west=-120,south=34,east=-118,north=35,resolution=1)
    intervals=[]
    for h in range(72):
        values=np.array([[h/100,NODATA]],dtype='float32')
        p=tmp_path/'risk.tif';info=write_cog(p,values,g,['risk_score'],['1'])
        key=f'raster/v1/runs/test/risk/{h:03}.tif';store.put(key,p.read_bytes())
        intervals.append({'risk':{**info,'key':key},'start':f'hour-{h}','end':f'hour-{h+1}'})
    m={'batch_id':'test','grid':g.model_dump(),'fixture':False,'quality_status':'complete','intervals':intervals,
        'selected_run':'2026-11-18T00:00:00+00:00','target_start':'2026-11-18T06:00:00+00:00','cutoff':'2026-11-18T05:30:00+00:00'}
    store.put('raster/v1/latest.json',json.dumps({'batch_id':'test'}).encode())
    return m,store


def test_weather_windows_use_raster_max_and_preserve_nodata(tmp_path):
    m,store=manifest_and_store(tmp_path)
    for start,want in [(0,.23),(24,.47),(48,.71)]:
        values=weather_window(m,store,start)
        assert values[0,0]==pytest.approx(want)
        assert values[0,1]==NODATA
    # Corrupt input cannot silently reach a public image.
    store.path(m['intervals'][0]['risk']['key']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):weather_window(m,store,0)


def test_weather_map_rejects_fixture_and_obsolete_publications(tmp_path):
    m,store=manifest_and_store(tmp_path);public=LocalStore(tmp_path/'public')
    with pytest.raises(ValueError,match='fixture'):publish_weather_map({**m,'fixture':True},store,public)
    store.replace_pointer('raster/v1/latest.json',b'{"batch_id":"newer"}',store.read('raster/v1/latest.json')[1])
    assert publish_weather_map(m,store,public) is False
    assert public.read('weather/latest.json')[0] is None


def test_weather_map_publishes_projected_images_and_window_metadata(tmp_path):
    m,store=manifest_and_store(tmp_path);public=LocalStore(tmp_path/'public')
    assert publish_weather_map(m,store,public)
    metadata=json.loads(public.read('weather/latest.json')[0])
    assert set(metadata['windows'])=={'next_24h','next_24_48h','next_48_72h'}
    assert metadata['fixture'] is False and metadata['calibrated'] is False
    assert metadata['grid_resolution_degrees']==1
    spec=metadata['windows']['next_24h']
    assert spec['start']=='hour-0' and spec['end']=='hour-24'
    with MemoryFile(public.read(spec['image'])[0]) as f:
        with f.open() as src:
            rgba=src.read()
            assert rgba.shape[0]==4
            assert rgba[3,:,:rgba.shape[2]//2].max()>0
            assert rgba[3,:,rgba.shape[2]//2:].max()==0
