import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.io import MemoryFile
import pytest
from raster_pipeline.canopy_tiles import EXTENT,read_tile,build_tiles
from raster_pipeline.download import file_sha


def test_canopy_tiles_preserve_xyz_orientation_zero_and_transparency(tmp_path):
    path=tmp_path/'canopy.tif'
    with rasterio.open(path,'w',driver='GTiff',width=2,height=2,count=1,dtype='uint8',
            crs='EPSG:3857',transform=from_bounds(-EXTENT,-EXTENT,EXTENT,EXTENT,2,2),nodata=255) as dst:
        dst.write(np.array([[0,255],[100,50]],dtype='uint8'),1)
    with rasterio.open(path) as src:
        rgba=read_tile(src,0,0,0)
    assert rgba[3,64,64]>0
    assert rgba[3,64,192]==0
    assert rgba[1,192,64]<rgba[1,192,192]
    metadata=build_tiles(path,tmp_path/'out',sha256=file_sha(path),vintage='synthetic test',maxzoom=0,workers=1)
    tile=tmp_path/'out'/metadata['tiles'].format(z=0,x=0,y=0)
    with MemoryFile(tile.read_bytes()) as mem:
        with mem.open() as src:np.testing.assert_array_equal(src.read(),rgba)
    assert (tmp_path/'out/canopy/latest.json').exists()
    with pytest.raises(ValueError,match='checksum'):
        build_tiles(path,tmp_path/'wrong',sha256='0'*64,vintage='synthetic test',maxzoom=0)
