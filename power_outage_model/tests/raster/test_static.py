import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box,LineString
from raster_pipeline.canopy import aggregate_canopy
from raster_pipeline.assets import transmission_exposure
from raster_pipeline.static import build_static,load_static
from raster_pipeline.contracts import GridSpec


def test_area_weighted_canopy_zero_special_and_coast(tmp_path):
    # Projected equal-area pixels: two 100m² valid pieces and one special code.
    path=tmp_path/'canopy.tif'
    with rasterio.open(path,'w',driver='GTiff',height=1,width=3,count=1,dtype='uint8',crs='EPSG:5070',transform=from_origin(0,10,10,10),nodata=255) as dst:
        dst.write(np.array([[[0,100,255]]],dtype='uint8'))
    mean,fraction=aggregate_canopy(path,[box(0,0,20,10),box(0,0,30,10),box(0,0,5,10)],chunk_size=1)
    np.testing.assert_allclose(mean[:2],[.5,.5]); assert mean[2]==0
    np.testing.assert_allclose(fraction,[1,2/3,1])


def test_clipped_lengths_dedup_status_and_unknown_inventory():
    cells=[box(-120,34,-119,35),box(-119,34,-118,35),box(-118,34,-117,35)]
    pilot=box(-120,34,-118.5,35)
    line=LineString([(-119.8,34.5),(-118.2,34.5)])
    features=[{'id':'a','geometry':line,'properties':{'Status':'Operational','Type':'OH','Owner':'A','kV':'115','Circuit':'Double'}}]
    result,coverage,inventory=transmission_exposure(features*2,cells,pilot,pilot)
    assert 70<result[0,0]<75 and 40<result[0,1]<50
    assert result[0,2]==-9999 and coverage.tolist()==[1,1,0]
    np.testing.assert_allclose(result[1:,0].sum(),result[0,0])
    assert inventory[0]['circuit_count']==2
    features[0]['properties']['Status']='Proposed'
    out,_,_=transmission_exposure(features,cells,pilot,pilot)
    assert out[0,0]==0
    _,coverage,_=transmission_exposure([],cells,pilot,box(-120,34,-119,35))
    assert coverage.tolist()==[1,2,0]


def test_partial_overlapping_duplicate_routes_not_double_counted():
    cells=[box(-120,34,-119,35)];pilot=cells[0]
    def f(a,b,i):return {'id':i,'geometry':LineString([(a,34.5),(b,34.5)]),'properties':{'Status':'Operational','Type':'OH'}}
    one,_,_=transmission_exposure([f(-119.9,-119.1,'a')],cells,pilot,pilot)
    overlaps,_,_=transmission_exposure([f(-119.9,-119.5,'b'),f(-119.6,-119.1,'c')],cells,pilot,pilot)
    np.testing.assert_allclose(one,overlaps,rtol=1e-5)




def test_geographic_canopy_uses_area_instead_of_pixel_count(tmp_path):
    path=tmp_path/'geographic.tif'
    # Two latitude rows have very different physical areas.
    with rasterio.open(path,'w',driver='GTiff',height=2,width=1,count=1,dtype='uint8',crs='EPSG:4326',transform=from_origin(-120,60,1,20),nodata=255) as dst:
        dst.write(np.array([[[100],[0]]],dtype='uint8'))
    mean,valid=aggregate_canopy(path,[box(-120,20,-119,60)],chunk_size=1)
    assert .39<mean[0]<.46
    assert valid[0]==pytest.approx(1,abs=1e-4)


def test_build_static_from_pinned_real_format_sources(tmp_path):
    import json
    from shapely.geometry import mapping
    from raster_pipeline.storage import LocalStore
    from raster_pipeline.download import file_sha
    g=GridSpec(grid_id='static-build-test',west=-120,south=34,east=-118,north=35,resolution=1)
    canopy=tmp_path/'source.tif'
    with rasterio.open(canopy,'w',driver='GTiff',width=2,height=1,count=1,dtype='uint8',crs='EPSG:4326',transform=g.transform,nodata=255) as out:
        out.write(np.array([[[50,0]]],dtype='uint8'))
    sources={}
    for name in ('canopy','analysis_land','california','assessed','transmission'):
        path=canopy if name=='canopy' else tmp_path/(name+'.geojson')
        if name!='canopy':
            geometry=LineString([(-119.9,34.5),(-119.1,34.5)]) if name=='transmission' else box(-120,34,-118 if name=='analysis_land' else -119,35)
            path.write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','geometry':mapping(geometry),'properties':{'Status':'Operational','Type':'OH'}}]}))
        sources[name]={'path':str(path),'sha256':file_sha(path),'uri':'fixture://'+path.name,'vintage':'CC0 test','attribution':'CC0 test','license':'CC0'}
    sources['canopy']['crs']='EPSG:4326'
    config={'sources':sources,'canopy_valid_range':[0,100],'canopy_special_codes':[255]}
    store=LocalStore(tmp_path/'store');manifest=build_static(config,g,store);s=load_static(manifest,g,store)
    np.testing.assert_allclose(s.canopy,[[.5,0]])
    assert s.coverage[2].tolist()==[[1,0]]
    assert s.exposure[0,0,0]>0 and s.exposure[0,0,1]==-9999
    assert build_static(config,g,store)==manifest
