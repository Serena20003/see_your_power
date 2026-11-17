"""Single training/inference transform; complete, versioned 24-hour summaries."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import numpy as np
from scipy.ndimage import maximum_filter, convolve
from raster_pipeline.contracts import NODATA, digest
from .contracts import HORIZONS, FeatureTable, RowKey, time

REGISTRY_PATH=Path(__file__).parent/'configs/features-v1.json'


def load_registry(): return json.loads(REGISTRY_PATH.read_text())


def render_feature_docs(registry):
    lines=['# Feature registry v1','',f"Registry SHA256: `{digest(registry)}`",'',
        'Predictors are ordered exactly as below. Geographic identifiers remain metadata.','']
    for f in registry['features']:
        lines.extend([f"## {f['name']}",'']+[f"- {k}: {v}" for k,v in f.items() if k!='name']+[''])
    return '\n'.join(lines)


def analysis_land_area(batch):
    from shapely.geometry import box, shape
    from shapely.ops import transform
    from pyproj import Transformer
    grid=batch.grid; static=batch.static
    footprint=batch.provenance.get('analysis_land_geometry')
    if footprint is None:
        if not batch.ref.fixture: raise ValueError('verified analysis land geometry required for land area')
        # Test-only exact cells; live data always uses the verified footprint.
        land=None
    else: land=shape(footprint)
    project=Transformer.from_crs(4326,5070,always_xy=True).transform
    result=np.zeros(grid.shape,dtype=float)
    for r,c in zip(*np.where(static.analysis_mask)):
        cell=box(grid.west+c*grid.resolution,grid.north-(r+1)*grid.resolution,
                 grid.west+(c+1)*grid.resolution,grid.north-r*grid.resolution)
        polygon=cell if land is None else cell.intersection(land)
        result[r,c]=transform(project,polygon.segmentize(.01)).area/1e6
    return result


def neighborhood(values,area,mask,valid):
    kernel=np.ones((3,3)); weights=np.where(mask,area,0)
    missing=convolve((mask&~valid).astype(int),kernel,mode='constant',cval=0)>0
    numerator=convolve(np.where(mask&valid,values*area,0),kernel,mode='constant',cval=0)
    denominator=convolve(weights,kernel,mode='constant',cval=0)
    mean=np.divide(numerator,denominator,out=np.full_like(numerator,np.nan),where=denominator>0)
    peak=maximum_filter(np.where(mask&valid,values,-np.inf),size=3,mode='constant',cval=-np.inf)
    return np.where(missing,np.nan,peak),np.where(missing,np.nan,mean)


def build_features(batch,registry=None):
    registry=registry or load_registry()
    if digest(registry)!=digest(load_registry()): raise ValueError('unknown feature registry/order; explicit new version required')
    m=batch.manifest; cutoff=time(m['cutoff']); target=time(m['target_start'])
    fields=batch.provenance['inputs'].get('fields',[])
    if not fields: raise ValueError('weather input availability evidence required')
    for f in fields:
        if time(f['first_seen'])>cutoff or time(f['run'])>cutoff or f.get('metadata',{}).get('kind','forecast')!='forecast':
            raise ValueError('post-cutoff or observed weather forbidden')
    if len(m['intervals'])!=72 or batch.weather.shape!=(72,3,*batch.grid.shape): raise ValueError('exactly 72 weather hours required')
    for i,item in enumerate(m['intervals']):
        if time(item['start'])!=target+timedelta(hours=i) or time(item['end'])!=target+timedelta(hours=i+1):
            raise ValueError('noncontiguous target intervals')
    canopy_source=batch.static.manifest.get('sources',{}).get('canopy',{})
    available=canopy_source.get('available_at')
    retrospective=not batch.ref.fixture and (available is None or time(available)>cutoff)
    mask=batch.static.analysis_mask
    canopy=np.asarray(batch.static.canopy,dtype=float)
    area=analysis_land_area(batch)
    base_valid=mask&np.isfinite(canopy)&(canopy>=0)&(canopy<=1)&(area>0)
    rows=[]; keys=[]; eligible=[]; metadata=[]
    names=tuple(f['name'] for f in registry['features'])
    for h,horizon in enumerate(HORIZONS):
        w=batch.weather[h*24:(h+1)*24].astype(float)
        weather_valid=(np.isfinite(w)&(w!=NODATA)).all(axis=(0,1))
        valid=base_valid&weather_valid
        rain,temp,wind=w[:,0],w[:,1],w[:,2]
        valid&=(rain>=0).all(axis=0)&(wind>=0).all(axis=0)
        values={'wind_max_ms':wind.max(0),'wind_mean_ms':wind.mean(0),
            'rain_total_mm':rain.sum(0),'rain_max_1h_mm':rain.max(0),
            'temp_min_c':temp.min(0),'temp_max_c':temp.max(0),'temp_mean_c':temp.mean(0),
            'temp_range_c':temp.max(0)-temp.min(0),'canopy_fraction':canopy,
            'analysis_land_area_km2':area}
        values['neighbor_wind_max_ms'],values['neighbor_wind_mean_ms']=neighborhood(values['wind_max_ms'],area,mask,valid)
        values['neighbor_rain_max_mm'],values['neighbor_rain_mean_mm']=neighborhood(values['rain_total_mm'],area,mask,valid)
        _,values['neighbor_canopy_mean']=neighborhood(canopy,area,mask,valid)
        midpoint=target+timedelta(hours=h*24+12)
        begin=datetime(midpoint.year,1,1,tzinfo=midpoint.tzinfo); end=begin.replace(year=begin.year+1)
        phase=2*np.pi*(midpoint-begin).total_seconds()/(end-begin).total_seconds()
        values['season_sin']=np.full(mask.shape,np.sin(phase));values['season_cos']=np.full(mask.shape,np.cos(phase))
        matrix=np.stack([values[n] for n in names],axis=-1).reshape(-1,len(names))
        ok=valid.reshape(-1)&np.isfinite(matrix).all(1)&~np.full(mask.size,retrospective)
        for j,f in enumerate(registry['features']):
            lo,hi=f['valid_range'];ok&=(matrix[:,j]>=lo)&(matrix[:,j]<=hi)
        rows.append(matrix.astype('float32'));eligible.append(ok)
        for r in range(batch.grid.height):
            for c in range(batch.grid.width):
                keys.append(RowKey(archive_id=batch.ref.archive_id,grid_id=batch.grid.grid_id,row=r,col=c,horizon_id=horizon))
                metadata.append({'cutoff':cutoff.isoformat(),'start':(target+timedelta(hours=h*24)).isoformat(),
                    'end':(target+timedelta(hours=(h+1)*24)).isoformat(),'season':f'Q{(midpoint.month-1)//3+1}',
                    'retrospective_only':retrospective,'analysis_land':bool(mask[r,c])})
    return FeatureTable(names=names,values=np.concatenate(rows),keys=tuple(keys),eligible=np.concatenate(eligible),
        metadata=tuple(metadata),fixture=batch.ref.fixture,provenance={'registry_hash':digest(registry),'archive_id':batch.ref.archive_id,
            'static_version':m['static_version'],'canopy_vintage':m.get('canopy_vintage'),
            'normalization_reproducible':batch.ref.normalization_reproducible,'retrospective_only':retrospective})
