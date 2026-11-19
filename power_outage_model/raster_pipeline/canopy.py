"""Exact overlap-area aggregation in the source's equal-area projection.

Only a bounded source window is read at once. Interior blocks use scalar
pixel areas; only boundary blocks need vectorized polygon intersections.
"""
import math
import logging
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.windows import transform as window_transform
from rasterio.features import rasterize
import shapely
from shapely.geometry import box
from pyproj import CRS,Transformer


def _aggregate_chunk(args):
    path,polygons,chunk_size,valid_range,special_codes=args
    return aggregate_canopy(path,polygons,chunk_size=chunk_size,valid_range=valid_range,special_codes=special_codes)


def _polygonal(geometry):
    if geometry.geom_type in ('Polygon','MultiPolygon'):return geometry
    return shapely.union_all([_polygonal(part) for part in getattr(geometry,'geoms',())])


def aggregate_canopy(path,land_polygons,*,chunk_size=256,valid_range=(0,100),special_codes=(255,),workers=1):
    if chunk_size<1:raise ValueError('positive chunk size required')
    if not 1<=workers<=16:raise ValueError('canopy workers must be between 1 and 16')
    if workers>1:
        groups=[(path,land_polygons[i:i+256],chunk_size,valid_range,special_codes) for i in range(0,len(land_polygons),256)]
        means=[];fractions=[]
        with ProcessPoolExecutor(max_workers=workers,mp_context=get_context('spawn')) as pool:
            for a,b in pool.map(_aggregate_chunk,groups):
                means.extend(a);fractions.extend(b)
                logging.getLogger('raster_pipeline').info('Canopy aggregation: %s/%s grid cells',len(means),len(land_polygons))
        return np.asarray(means,dtype='float32'),np.asarray(fractions,dtype='float32')
    means=[];fractions=[]
    with rasterio.open(path) as src:
        # Areas below are planar; accepting geographic data would bias the mean.
        crs=CRS(src.crs)
        operation=crs.coordinate_operation
        geographic=crs.to_epsg()==4326
        equal_area=crs.is_projected and operation is not None and 'equal area' in operation.method_name.lower()
        if not (geographic or equal_area):
            raise ValueError('canopy source must use WGS84 geographic or projected equal-area CRS')
        area_transform=Transformer.from_crs(crs,6933,always_xy=True) if geographic else None
        def area_of(geometry):
            return shapely.area(shapely.transform(geometry,area_transform.transform,interleaved=False)) if geographic else shapely.area(geometry)
        t=src.transform
        if t.b or t.d or t.a<=0 or t.e>=0:raise ValueError('north-up canopy raster required')
        pixel_area=abs(t.a*t.e)
        for land in land_polygons:
            if len(means)%1000==0:
                logging.getLogger('raster_pipeline').info('Canopy aggregation: %s/%s grid cells',len(means),len(land_polygons))
            if land.is_empty or land.area<=0:
                means.append(-9999.);fractions.append(-9999.);continue
            # Exact clipping can retain zero-area lines/points alongside land.
            # Their removal preserves area and gives rasterization a surface.
            land=_polygonal(land)
            shapely.prepare(land)
            polygon_geojson=shapely.geometry.mapping(land)
            boundary_geojson=shapely.geometry.mapping(land.boundary)
            x0,y0,x1,y1=land.bounds
            c0=max(0,math.floor((x0-t.c)/t.a));c1=min(src.width,math.ceil((x1-t.c)/t.a))
            r0=max(0,math.floor((y1-t.f)/t.e));r1=min(src.height,math.ceil((y0-t.f)/t.e))
            weighted=0.;valid_area=0.
            for row in range(r0,r1,chunk_size):
                for col in range(c0,c1,chunk_size):
                    height=min(chunk_size,r1-row);width=min(chunk_size,c1-col)
                    block=box(t.c+col*t.a,t.f+(row+height)*t.e,t.c+(col+width)*t.a,t.f+row*t.e)
                    if not land.intersects(block):continue
                    data=src.read(1,window=Window(col,row,width,height),masked=True)
                    ok=~np.ma.getmaskarray(data)&np.isfinite(data.data)&(data.data>=valid_range[0])&(data.data<=valid_range[1])
                    ok &= ~np.isin(data.data,special_codes)
                    if land.covers(block):
                        if geographic:
                            ymax=t.f+np.arange(row,row+height)*t.e
                            rows=shapely.box(t.c,ymax+t.e,t.c+t.a,ymax)
                            areas=np.broadcast_to(area_of(rows)[:,None],(height,width))
                        else:areas=np.full((height,width),pixel_area)
                    else:
                        local_transform=window_transform(Window(col,row,width,height),t)
                        inside=rasterize([(polygon_geojson,1)],out_shape=(height,width),transform=local_transform,dtype='uint8').astype(bool)
                        boundary=rasterize([(boundary_geojson,1)],out_shape=(height,width),transform=local_transform,all_touched=True,dtype='uint8').astype(bool)
                        if geographic:
                            ymax=t.f+np.arange(row,row+height)*t.e
                            rows=shapely.box(t.c,ymax+t.e,t.c+t.a,ymax)
                            full_areas=np.broadcast_to(area_of(rows)[:,None],(height,width))
                        else:full_areas=pixel_area
                        areas=np.where(inside,full_areas,0).astype('float64')
                        rows,cols=np.nonzero(boundary)
                        xmin=t.c+(cols+col)*t.a;ymax=t.f+(rows+row)*t.e
                        pixels=shapely.box(xmin,ymax+t.e,xmin+t.a,ymax)
                        areas[boundary]=area_of(shapely.intersection(pixels,land))
                    area=np.where(ok,areas,0)
                    weighted+=float(np.sum(np.where(ok,data.data,0)*area,dtype='float64'))
                    valid_area+=float(np.sum(area,dtype='float64'))
            means.append(weighted/valid_area/100 if valid_area>0 else -9999.)
            fractions.append(min(valid_area/float(area_of(land)),1.))
    return np.asarray(means,dtype='float32'),np.asarray(fractions,dtype='float32')
