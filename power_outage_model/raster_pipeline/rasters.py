from pathlib import Path
import tempfile
import numpy as np
import rasterio
from rasterio.shutil import copy as raster_copy
from .contracts import NODATA,WEATHER_BANDS,WEATHER_UNITS,WEATHER_SEMANTICS
from .download import file_sha


def write_cog(path,values,grid,bands,units,*,tags=None,coverage=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    a=np.asarray(values)
    if a.ndim==2:a=a[None]
    if a.shape!=(len(bands),*grid.shape) or len(units)!=len(bands):raise ValueError('raster dimensions/bands mismatch')
    dtype='uint8' if coverage else 'float32';nodata=255 if coverage else NODATA
    a=a.astype(dtype)
    if not np.isfinite(a).all():raise ValueError('nonfinite raster values must be explicit NoData')
    with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
        source=Path(tmp)/'source.tif';target=Path(tmp)/'cog.tif'
        with rasterio.open(source,'w',driver='GTiff',width=grid.width,height=grid.height,
            count=len(bands),dtype=dtype,crs=grid.crs,transform=grid.transform,nodata=nodata,
            tiled=True,blockxsize=256,blockysize=256,compress='DEFLATE') as out:
            out.write(a)
            out.update_tags(grid_id=grid.grid_id,**(tags or {}))
            for i,(band,unit) in enumerate(zip(bands,units),1):
                out.set_band_description(i,band);out.set_band_unit(i,unit)
        raster_copy(source,target,driver='COG',BLOCKSIZE=256,COMPRESS='DEFLATE',
            OVERVIEW_RESAMPLING='NEAREST',RESAMPLING='NEAREST')
        # The store, rather than this staging writer, enforces immutability.
        target.replace(path)
    return {'sha256':file_sha(path),'bytes':path.stat().st_size,
        'valid_cell_counts':[int(np.sum(b!=nodata)) for b in a]}


def write_weather_cog(path,values,grid,interval):
    return write_cog(path,values,grid,WEATHER_BANDS,WEATHER_UNITS,tags={
        'interval_start':interval.start.isoformat(),'interval_end':interval.end.isoformat(),
        **{f'{band}_temporal_semantics':semantic for band,semantic in zip(WEATHER_BANDS,WEATHER_SEMANTICS)}})


def write_risk_cog(path,result,grid,interval):
    if result.metadata!={'value_kind':'heuristic_score','calibrated':False,'asset_conditioned':False,'method':'environmental_heuristic_v2'}:
        raise ValueError('risk metadata may not claim probability or calibration')
    return write_cog(path,result.score,grid,['risk_score'],['1'],tags={
        'interval_start':interval.start.isoformat(),'interval_end':interval.end.isoformat(),
        'value_kind':'heuristic_score','calibrated':'false','asset_conditioned':'false',
        'method':'environmental_heuristic_v2'})


def validate_cog(path,grid,bands,units,*,coverage=False):
    from rio_cogeo.cogeo import cog_validate
    good,errors,_=cog_validate(str(path),strict=True)
    if not good:raise ValueError(f'invalid COG: {errors}')
    with rasterio.open(path) as src:
        if (src.width,src.height)!=(grid.width,grid.height) or src.crs!=rasterio.crs.CRS.from_string(grid.crs) or src.transform!=grid.transform or src.tags().get('grid_id')!=grid.grid_id:
            raise ValueError('raster grid mismatch')
        if src.descriptions!=tuple(bands) or src.units!=tuple(units):raise ValueError('raster band/unit mismatch')
        if src.nodata!=(255 if coverage else NODATA) or set(src.dtypes)!={('uint8' if coverage else 'float32')}:
            raise ValueError('raster dtype/NoData mismatch')
        if any(s!=(256,256) for s in src.block_shapes) or src.compression.value!='DEFLATE':raise ValueError('raster block/compression mismatch')
        if max(grid.shape)>256 and not src.overviews(1):raise ValueError('missing overviews')
        a=src.read()
        if not np.isfinite(a).all():raise ValueError('nonfinite raster values')
        return a,src.tags()
