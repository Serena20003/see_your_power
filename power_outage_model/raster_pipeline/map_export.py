"""Public raster presentation assets; scientific COGs remain private."""
from datetime import datetime, timezone
import hashlib
import json

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds, Resampling

from .contracts import GridSpec, NODATA, canonical

WINDOWS=('next_24h','next_24_48h','next_48_72h')


def colorize(values,kind):
    a=np.asarray(values,dtype='float32')
    if kind=='canopy':
        maximum=100
        stops=[(0,(255,255,255,90)),(50,(100,180,80,165)),(100,(20,100,20,200))]
    elif kind=='weather':
        maximum=1
        stops=[(0,(0,20,220,150)),(.25,(0,180,200,170)),(.5,(230,230,0,180)),
            (.75,(255,140,0,190)),(1,(220,30,30,200))]
    else:raise ValueError('unknown map palette')
    valid=np.isfinite(a)&(a>=0)&(a<=maximum)
    safe=np.where(valid,a,0)
    rgba=np.stack([np.interp(safe,[s[0] for s in stops],[s[1][b] for s in stops]) for b in range(4)]).astype('uint8')
    rgba[:,~valid]=0
    return rgba


def png_bytes(rgba):
    with MemoryFile() as mem:
        with mem.open(driver='PNG',width=rgba.shape[2],height=rgba.shape[1],count=4,dtype='uint8') as dst:
            dst.write(rgba)
            dst.colorinterp=(rasterio.enums.ColorInterp.red,rasterio.enums.ColorInterp.green,
                rasterio.enums.ColorInterp.blue,rasterio.enums.ColorInterp.alpha)
        return mem.read()


def weather_window(manifest,store,start):
    if len(manifest['intervals'])!=72 or start not in (0,24,48):raise ValueError('complete 72-hour batch required')
    grid=GridSpec.model_validate(manifest['grid'])
    maximum=np.full(grid.shape,NODATA,dtype='float32')
    valid=np.ones(grid.shape,dtype=bool)
    for entry in manifest['intervals'][start:start+24]:
        obj=entry['risk'];raw,_=store.read(obj['key'])
        if raw is None or hashlib.sha256(raw).hexdigest()!=obj['sha256']:raise ValueError('risk checksum mismatch')
        with MemoryFile(raw) as f:
            with f.open() as src:
                if src.shape!=grid.shape or src.transform!=grid.transform or src.crs!=rasterio.crs.CRS.from_string(grid.crs):
                    raise ValueError('risk raster grid mismatch')
                a=src.read(1)
        valid &= np.isfinite(a)&(a>=0)&(a<=1)
        maximum=np.maximum(maximum,a)
    return np.where(valid,maximum,NODATA).astype('float32')


def weather_png(values,grid):
    bounds=transform_bounds(grid.crs,'EPSG:3857',grid.west,grid.south,grid.east,grid.north)
    width=max(1024,grid.width)
    height=max(1,round(width*(bounds[3]-bounds[1])/(bounds[2]-bounds[0])))
    target=np.full((height,width),NODATA,dtype='float32')
    reproject(values,target,src_transform=grid.transform,src_crs=grid.crs,src_nodata=NODATA,
        dst_transform=from_bounds(*bounds,width,height),dst_crs='EPSG:3857',dst_nodata=NODATA,
        resampling=Resampling.nearest)
    return png_bytes(colorize(target,'weather'))


def publish_weather_map(manifest,store,public_store):
    if manifest.get('fixture') is not False:raise ValueError('fixture weather cannot become a live map')
    if manifest.get('quality_status')!='complete':raise ValueError('complete weather required')
    current,_=store.read('raster/v1/latest.json')
    if current is None or json.loads(current)['batch_id']!=manifest['batch_id']:return False
    grid=GridSpec.model_validate(manifest['grid']);windows={}
    for i,name in enumerate(WINDOWS):
        image=weather_png(weather_window(manifest,store,i*24),grid)
        key=f"weather/batches/{manifest['batch_id']}/{name}.png"
        public_store.put(key,image)
        windows[name]={'image':key,'sha256':hashlib.sha256(image).hexdigest(),
            'start':manifest['intervals'][i*24]['start'],'end':manifest['intervals'][i*24+23]['end']}
    metadata={'schema_version':'weather-map/v1','batch_id':manifest['batch_id'],'fixture':False,
        'selected_run':manifest['selected_run'],'target_start':manifest['target_start'],
        'cutoff':manifest['cutoff'],'published_at':datetime.now(timezone.utc).isoformat(),
        'aggregation':'maximum hourly environmental risk per pixel in each 24-hour window',
        'value_kind':'heuristic_score','calibrated':False,'grid_resolution_degrees':grid.resolution,
        'coverage_policy':manifest.get('coverage_policy','complete_land'),
        'coverage_summary':manifest.get('coverage_summary'),
        'coordinates':[[grid.west,grid.north],[grid.east,grid.north],[grid.east,grid.south],[grid.west,grid.south]],
        'attribution':'Deutscher Wetterdienst (DWD), ICON Global; NLCD / USDA Forest Service',
        'windows':windows}
    # Share the scientific publication lock so an older renderer cannot advance
    # the public pointer after a newer scientific batch has won publication.
    with store.reference_lock():
        current,_=store.read('raster/v1/latest.json')
        if current is None or json.loads(current)['batch_id']!=manifest['batch_id']:return False
        _,generation=public_store.read('weather/latest.json')
        return public_store.replace_pointer('weather/latest.json',canonical(metadata),generation)
