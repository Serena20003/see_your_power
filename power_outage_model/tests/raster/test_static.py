import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box,LineString
from raster_pipeline.canopy import aggregate_canopy
from raster_pipeline.assets import transmission_exposure
from raster_pipeline.static import build_static,load_static
from raster_pipeline.contracts import GridSpec


@pytest.mark.parametrize('crs,origin,pixel',[('EPSG:5070',(0,20),1),('EPSG:4326',(-120,42),.1)])
def test_canopy_aggregation_matches_individual_exact_pixel_intersections(tmp_path,crs,origin,pixel):
    import shapely
    from shapely.geometry import Polygon
    from pyproj import Transformer
    x,y=origin;t=from_origin(x,y,pixel,pixel)
    values=(np.arange(400).reshape(20,20)%101).astype('uint8');values[::4,::3]=255
    path=tmp_path/'exact.tif'
    with rasterio.open(path,'w',driver='GTiff',width=20,height=20,count=1,dtype='uint8',crs=crs,transform=t,nodata=255) as dst:dst.write(values,1)
    points=[(x+.2*pixel,y-.1*pixel),(x+19.8*pixel,y-5.3*pixel),(x+15.2*pixel,y-19.9*pixel),(x+.2*pixel,y-15.4*pixel)]
    land=Polygon(points).difference(box(x+4.2*pixel,y-11.8*pixel,x+7.7*pixel,y-5.5*pixel))
    project=Transformer.from_crs(crs,6933,always_xy=True) if crs=='EPSG:4326' else None
    def area(g):return shapely.transform(g,project.transform,interleaved=False).area if project else g.area
    weighted=valid=0
    for r in range(20):
        for c in range(20):
            if values[r,c]==255:continue
            overlap=box(x+c*pixel,y-(r+1)*pixel,x+(c+1)*pixel,y-r*pixel).intersection(land)
            a=area(overlap);weighted+=a*values[r,c];valid+=a
    mean,fraction=aggregate_canopy(path,[land],chunk_size=7)
    assert mean[0]==pytest.approx(weighted/valid/100,rel=1e-6)
    assert fraction[0]==pytest.approx(valid/area(land),rel=1e-6)


def test_land_clipping_preserves_coast_lakes_islands_and_empty_cells():
    from raster_pipeline.static import clip_land_cells
    land=box(0,0,4,4).difference(box(1,1,2,2)).union(box(5,0,6,1))
    target=[box(.1,.1,.5,.5),box(-1,-1,0,0),box(-1,2,1,3),
        box(1,1,2,2),box(.5,.5,2.5,2.5),box(5,0,6,1),box(7,0,8,1)]
    result=clip_land_cells(target,land)
    assert all(a.equals(b.intersection(land)) for a,b in zip(result,target))


def test_area_weighted_canopy_zero_special_and_coast(tmp_path):
    # Projected equal-area pixels: two 100m² valid pieces and one special code.
    path=tmp_path/'canopy.tif'
    with rasterio.open(path,'w',driver='GTiff',height=1,width=3,count=1,dtype='uint8',crs='EPSG:5070',transform=from_origin(0,10,10,10),nodata=255) as dst:
        dst.write(np.array([[[0,100,255]]],dtype='uint8'))
    mean,fraction=aggregate_canopy(path,[box(0,0,20,10),box(0,0,30,10),box(0,0,5,10)],chunk_size=1)
    np.testing.assert_allclose(mean[:2],[.5,.5]); assert mean[2]==0
    np.testing.assert_allclose(fraction,[1,2/3,1])


def test_canopy_boundary_fast_path_preserves_subpixel_lake_hole(tmp_path):
    path=tmp_path/'canopy-hole.tif'
    with rasterio.open(path,'w',driver='GTiff',height=2,width=2,count=1,dtype='uint8',
            crs='EPSG:5070',transform=from_origin(0,2,1,1),nodata=255) as dst:
        dst.write(np.array([[[0,100],[50,255]]],dtype='uint8'))
    land=box(0,0,2,2).difference(box(.25,1.25,.75,1.75))
    mean,fraction=aggregate_canopy(path,[land],chunk_size=2)
    assert mean[0]==pytest.approx(150/2.75/100)
    assert fraction[0]==pytest.approx(2.75/3.75)
    parallel=aggregate_canopy(path,[land,box(0,0,1,1)],chunk_size=2,workers=2)
    serial=aggregate_canopy(path,[land,box(0,0,1,1)],chunk_size=2)
    for a,b in zip(parallel,serial):np.testing.assert_array_equal(a,b)
    from shapely.geometry import GeometryCollection
    mixed=GeometryCollection([land,LineString([(0,0),(3,3)])])
    for a,b in zip(aggregate_canopy(path,[mixed],chunk_size=2),(mean,fraction)):
        np.testing.assert_array_equal(a,b)


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


def test_static_release_roundtrip_and_grid_rejection(tmp_path):
    from raster_pipeline.storage import LocalStore
    from raster_pipeline.run import fixture_static
    g=GridSpec(grid_id='tiny',west=-120,south=34,east=-118,north=35,resolution=1)
    store=LocalStore(tmp_path)
    manifest=fixture_static(g,store)
    s=load_static(manifest,g,store)
    assert s.canopy.shape==(1,2) and np.all(s.coverage[1]==1)
    with pytest.raises(ValueError,match='grid'):load_static(manifest,GridSpec(),store)


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
