"""Exact overlap-area aggregation in the source's equal-area projection.

Only a bounded source window is read at once. Interior blocks use scalar
pixel areas; only boundary blocks need vectorized polygon intersections.
"""
import math
import numpy as np
import rasterio
from rasterio.windows import Window
import shapely
from shapely.geometry import box
from pyproj import CRS,Transformer


def aggregate_canopy(path,land_polygons,*,chunk_size=256,valid_range=(0,100),special_codes=(255,)):
    if chunk_size<1:raise ValueError('positive chunk size required')
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
            if land.is_empty or land.area<=0:
                means.append(-9999.);fractions.append(-9999.);continue
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
                        cols,rows=np.meshgrid(np.arange(col,col+width),np.arange(row,row+height))
                        xmin=t.c+cols*t.a;ymax=t.f+rows*t.e
                        pixels=shapely.box(xmin,ymax+t.e,xmin+t.a,ymax)
                        areas=area_of(shapely.intersection(pixels,land))
                    area=np.where(ok,areas,0)
                    weighted+=float(np.sum(np.where(ok,data.data,0)*area,dtype='float64'))
                    valid_area+=float(np.sum(area,dtype='float64'))
            means.append(weighted/valid_area/100 if valid_area>0 else -9999.)
            fractions.append(min(valid_area/float(area_of(land)),1.))
    return np.asarray(means,dtype='float32'),np.asarray(fractions,dtype='float32')
