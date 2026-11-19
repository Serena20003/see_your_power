"""Build static XYZ canopy PNGs from the pinned source raster, without counties."""
import argparse
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor
from multiprocessing import get_context
import json
import math
import os
from pathlib import Path
import threading

import numpy as np
import rasterio
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling

from .download import file_sha
from .map_export import colorize,png_bytes
from .storage import GCSStore

EXTENT=20037508.342789244
BOUNDS=(-125,24,-66,50)
_WORKER_SOURCES={}


def _tile_bytes(source,overviews,tile,cache,blank):
    z,x,y=tile
    latitude=math.atan(math.sinh(math.pi*(1-2*(y+.5)/2**z)))
    factor=2*EXTENT/(256*2**z)*math.cos(latitude)/30
    level=max([i for i,v in enumerate(overviews) if v<=factor],default=-1)
    if level not in cache:cache[level]=rasterio.open(source,**({'overview_level':level} if level>=0 else {}))
    rgba=read_tile(cache[level],z,x,y)
    return png_bytes(rgba) if rgba[3].any() else blank


def _render_local_tile(args):
    source,output,prefix,overviews,tile,blank=args
    z,x,y=tile;path=Path(output)/prefix/'tiles'/str(z)/str(x)/f'{y}.png'
    if path.exists():return
    with rasterio.Env(GDAL_CACHEMAX=64*1024*1024):
        raw=_tile_bytes(source,overviews,tile,_WORKER_SOURCES,blank)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_bytes(raw);tmp.replace(path)


def tile_range(z,bounds=BOUNDS):
    west,south,east,north=bounds;n=2**z
    def x(lon):return min(n-1,max(0,math.floor((lon+180)/360*n)))
    def y(lat):return min(n-1,max(0,math.floor((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)))
    return [(z,tx,ty) for tx in range(x(west),x(east)+1) for ty in range(y(north),y(south)+1)]


def read_tile(src,z,x,y):
    span=2*EXTENT/2**z
    bounds=(-EXTENT+x*span,EXTENT-(y+1)*span,-EXTENT+(x+1)*span,EXTENT-y*span)
    with WarpedVRT(src,crs='EPSG:3857',transform=from_bounds(*bounds,256,256),width=256,height=256,
            src_nodata=255,nodata=255,resampling=Resampling.average) as vrt:
        values=vrt.read(1,out_dtype='float32')
    return colorize(values,'canopy')


def build_tiles(source,output,*,sha256,vintage,maxzoom=12,workers=8):
    source=Path(source)
    remote=GCSStore(str(output)[5:].rstrip('/'),cache_control='public, max-age=31536000, immutable') if str(output).startswith('gs://') else None
    output=None if remote else Path(output)
    if not 0<=maxzoom<=14 or not 1<=workers<=16:raise ValueError('invalid tile generation limits')
    if file_sha(source)!=sha256:raise ValueError('canopy source checksum mismatch')
    prefix=f'canopy/{sha256[:24]}'
    tasks=[t for z in range(maxzoom+1) for t in tile_range(z)]
    local=threading.local()
    with rasterio.open(source) as src:overviews=src.overviews(1)
    blank=png_bytes(np.zeros((4,1,1),dtype='uint8'))
    def render(tile):
        z,x,y=tile;key=f'{prefix}/tiles/{z}/{x}/{y}.png'
        path=output/key if output else None
        if path and path.exists():return
        if not hasattr(local,'sources'):local.sources={}
        # Explicit overviews avoid scanning native CONUS pixels for world views.
        raw=_tile_bytes(source,overviews,tile,local.sources,blank)
        if remote:remote.put(key,raw)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            tmp=path.with_suffix('.tmp');tmp.write_bytes(raw);tmp.replace(path)
    if output is not None and workers>1:
        # PNG encoding and warping use separate processes to utilize CPUs while
        # keeping GDAL datasets private to each process.
        with ProcessPoolExecutor(max_workers=workers,mp_context=get_context('spawn')) as pool:
            for offset in range(0,len(tasks),256):
                list(pool.map(_render_local_tile,[(str(source),str(output),prefix,overviews,t,blank) for t in tasks[offset:offset+256]],chunksize=8))
                print(json.dumps({'stage':'canopy_tiles','complete':min(offset+256,len(tasks)),'total':len(tasks)}),flush=True)
    else:
        with rasterio.Env(GDAL_CACHEMAX=128*1024*1024),ThreadPoolExecutor(max_workers=workers) as pool:
            for offset in range(0,len(tasks),256):
                list(pool.map(render,tasks[offset:offset+256]))
                print(json.dumps({'stage':'canopy_tiles','complete':min(offset+256,len(tasks)),'total':len(tasks)}),flush=True)
    metadata={'schema_version':'canopy-map/v1','tiles':prefix+'/tiles/{z}/{x}/{y}.png',
        'bounds':list(BOUNDS),'minzoom':0,'maxzoom':maxzoom,'tile_size':256,'source_sha256':sha256,
        'source_resolution_m':30,'vintage':vintage,'resampling':'area average when downsampled',
        'attribution':'USDA Forest Service / MRLC NLCD Tree Canopy Cover; US Government public data'}
    if remote:
        _,generation=remote.read('canopy/latest.json')
        if not remote.replace_pointer('canopy/latest.json',json.dumps(metadata).encode(),generation):
            raise RuntimeError('canopy map publication changed concurrently')
    else:
        path=output/'canopy/latest.json';path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(metadata,indent=2));tmp.replace(path)
    return metadata


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--vintage',required=True);p.add_argument('--output',required=True)
    p.add_argument('--maxzoom',type=int,default=12);p.add_argument('--workers',type=int,default=8)
    a=p.parse_args()
    print(json.dumps(build_tiles(a.source,a.output,sha256=a.sha256,vintage=a.vintage,maxzoom=a.maxzoom,workers=a.workers)))


if __name__=='__main__':main()
