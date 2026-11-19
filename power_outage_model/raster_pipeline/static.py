import hashlib
import json
import math
from pathlib import Path
import tempfile
import numpy as np
import rasterio
import shapely
from shapely.geometry import box,shape,mapping,GeometryCollection
from shapely.ops import transform,unary_union
from pyproj import Transformer
from .contracts import StaticContext,StaticManifest,GridSpec,NODATA,canonical,digest
from .rasters import write_cog,validate_cog
from .download import file_sha
from .canopy import aggregate_canopy
from .assets import transmission_exposure

CANOPY_BANDS=('tree_canopy_fraction','valid_land_fraction')
ASSET_BANDS=('operational_route_km','overhead_route_km','underground_route_km','unknown_type_route_km')
COVERAGE_BANDS=('analysis_land','environmental_inputs','transmission_inventory')


def supported_land(context,*,allow_gaps=False):
    supported=context.analysis_mask&(context.coverage[1]==1)
    if not allow_gaps and np.any(context.analysis_mask&~supported):raise ValueError('insufficient canopy coverage')
    if not supported.any():raise ValueError('no land cells have sufficient environmental coverage')
    return supported


def coverage_summary(context):
    supported=context.analysis_mask&(context.coverage[1]==1)
    return {'analysis_land_cells':int(context.analysis_mask.sum()),'scored_land_cells':int(supported.sum()),
        'missing_environment_cells':int(np.sum(context.analysis_mask&~supported)),
        'minimum_valid_land_fraction':.95}


def cells(grid):
    return [box(grid.west+c*grid.resolution,grid.north-(r+1)*grid.resolution,
        grid.west+(c+1)*grid.resolution,grid.north-r*grid.resolution)
        for r in range(grid.height) for c in range(grid.width)]


def clip_land_cells(target_cells,land):
    # Prepared predicates avoid intersecting the full coastline for inland and
    # offshore cells. Boundary cells still use the exact original geometry.
    shapely.prepare(land)
    west,south,east,north=land.bounds
    # Exact coarse partitions make coastal intersections local instead of
    # repeatedly scanning every vertex of the continental mainland polygon.
    pieces=[land.intersection(box(x,y,x+5,y+5))
        for x in range(math.floor(west/5)*5,math.ceil(east),5)
        for y in range(math.floor(south/5)*5,math.ceil(north),5)]
    tree=shapely.STRtree(pieces)
    return [c if land.covers(c) else unary_union([c.intersection(pieces[i]) for i in tree.query(c)])
        if land.intersects(c) else GeometryCollection() for c in target_cells]


def save_static(context,sources,recipe,store,*,fixture=False,artifacts=None):
    version=context.version;prefix=f'raster/v1/static/{version}'
    objects={}
    with tempfile.TemporaryDirectory() as tmp:
        for name,values,bands,units,coverage in [
            ('canopy',np.stack([context.canopy,context.valid_land_fraction]),CANOPY_BANDS,['1','1'],False),
            ('california_transmission',context.exposure,ASSET_BANDS,['km']*4,False),
            ('coverage',context.coverage,COVERAGE_BANDS,['code']*3,True)]:
            path=Path(tmp)/(name+'.tif')
            info=write_cog(path,values,context.grid,bands,units,tags={'static_version':version},coverage=coverage)
            key=prefix+'/'+path.name;store.put(key,path.read_bytes());objects[name]={**info,'key':key}
    for name,data in (artifacts or {}).items():
        key=prefix+'/'+name;raw=canonical(data);store.put(key,raw)
        objects[name]={'key':key,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    manifest=StaticManifest(static_version=version,grid=context.grid,objects=objects,sources=sources,recipe=recipe,fixture=fixture)
    store.put(prefix+'/manifest.json',canonical(manifest))
    store.put(f'raster/v1/grids/{context.grid.grid_id}/grid.json',canonical(context.grid))
    return manifest


def load_static(manifest,grid,store):
    if isinstance(manifest,dict):manifest=StaticManifest.model_validate(manifest)
    if manifest.grid!=grid:raise ValueError('static grid/version mismatch')
    arrays={}
    with tempfile.TemporaryDirectory() as tmp:
        for name,bands,units,coverage in [('canopy',CANOPY_BANDS,['1','1'],False),
            ('california_transmission',ASSET_BANDS,['km']*4,False),('coverage',COVERAGE_BANDS,['code']*3,True)]:
            obj=manifest.objects[name];raw,_=store.read(obj['key'])
            if raw is None or hashlib.sha256(raw).hexdigest()!=obj['sha256']:raise ValueError('static checksum mismatch')
            path=Path(tmp)/(name+'.tif');path.write_bytes(raw)
            a,tags=validate_cog(path,grid,bands,units,coverage=coverage)
            if tags.get('static_version')!=manifest.static_version:raise ValueError('static version mismatch')
            arrays[name]=a
    canopy=arrays['canopy'];cov=arrays['coverage'];exposure=arrays['california_transmission']
    if not np.isin(cov[0],[0,1]).all() or not np.isin(cov[1],[0,1,2]).all() or not np.isin(cov[2],[0,1,2]).all():raise ValueError('invalid static coverage code')
    land=cov[0]==1
    if not land.any():raise ValueError('empty analysis land domain')
    env=(canopy[0]>=0)&(canopy[0]<=1)&(canopy[1]>=.95)&(canopy[1]<=1)
    if np.any((cov[1]==1)!=(land&env)) or np.any((~land)&(cov[1]!=0)):
        raise ValueError('inconsistent environmental coverage')
    if np.any(exposure[:,cov[2]!=1]!=NODATA) or np.any(exposure[:,cov[2]==1]<0):raise ValueError('inconsistent pilot inventory coverage')
    if not np.allclose(exposure[0,cov[2]==1],exposure[1:,cov[2]==1].sum(axis=0),atol=1e-3):raise ValueError('asset route bands do not sum')
    return StaticContext(grid,manifest.static_version,canopy[0],canopy[1],land,exposure,cov,manifest.model_dump(mode='json'))


def build_static(config,grid,store):
    """Build from pinned local source archives. All GeoJSON uses EPSG:4326.

    Require a land-only authoritative boundary (not a gross state rectangle).
    Sources are deliberately not auto-upgraded or guessed from catalogs.
    """
    for name in ('canopy','analysis_land','california','assessed','transmission'):
        src=config['sources'][name]
        if not src.get('sha256') or file_sha(src['path'])!=src['sha256']:raise ValueError(f'{name}: exact source checksum required')
        for key in ('uri','vintage','attribution','license'):
            if not src.get(key):raise ValueError(f'{name}: missing {key}')
    sources=config['sources']
    def geo(name):
        obj=json.loads(Path(sources[name]['path']).read_text())
        return unary_union([shape(f['geometry']) for f in obj['features']])
    land=geo('analysis_land');pilot=geo('california');assessed=geo('assessed')
    target_cells=cells(grid)
    with rasterio.open(sources['canopy']['path']) as src:
        if src.crs.to_string()!=sources['canopy']['crs']:raise ValueError('canopy source CRS does not match pin')
        project=Transformer.from_crs(4326,src.crs,always_xy=True).transform
    land_cells=[transform(project,c.segmentize(.005)) for c in clip_land_cells(target_cells,land)]
    canopy,valid=aggregate_canopy(sources['canopy']['path'],land_cells,
        valid_range=tuple(config['canopy_valid_range']),special_codes=tuple(config['canopy_special_codes']),workers=config.get('canopy_workers',1))
    features=json.loads(Path(sources['transmission']['path']).read_text())['features']
    exposure,pilot_cov,inventory=transmission_exposure(features,target_cells,pilot,assessed)
    mask=np.asarray([not c.is_empty and c.area>0 for c in land_cells])
    environmental=np.where(mask,np.where((valid>=.95)&(canopy>=0),1,2),0)
    coverage=np.stack([mask.astype('uint8'),environmental.astype('uint8'),pilot_cov]).reshape(3,*grid.shape)
    recipe={'version':'static-area-v1','grid':grid.model_dump(),'valid_range':config['canopy_valid_range'],
        'special_codes':config['canopy_special_codes'],'minimum_land_coverage':.95,
        'asset_overlap_precedence':['overhead','underground','unknown'],'config_hash':digest(config)}
    version=config.get('static_version') or digest({'sources':sources,'recipe':recipe})[:24]
    context=StaticContext(grid,version,canopy.reshape(grid.shape),valid.reshape(grid.shape),mask.reshape(grid.shape),exposure.reshape(4,*grid.shape),coverage)
    return save_static(context,sources,recipe,store,artifacts={
        'inventory.json':inventory,'pilot-footprint.geojson':mapping(pilot),
        'assessed-footprint.geojson':mapping(assessed),'analysis-land.geojson':mapping(land)})
