"""Deterministic uniform equal-area county-polygon proxies, never incident points."""
import hashlib
import numpy as np
from shapely.geometry import Point
from shapely.ops import transform
from pyproj import Transformer
from .contracts import SamplingPolicy

_PROJECT=Transformer.from_crs(4326,5070,always_xy=True).transform
_INVERSE=Transformer.from_crs(5070,4326,always_xy=True).transform
_CACHE={}


def stable_seed(parent_id,seed):return int.from_bytes(hashlib.sha256(f'{seed}:{parent_id}'.encode()).digest()[:8],'big')


def projected_geometry(county_fips,geometry):
    identity=(county_fips,hash(geometry.wkb))
    if identity not in _CACHE:
        polygon=transform(_PROJECT,geometry)
        if not polygon.is_valid or polygon.is_empty or polygon.area<=0:raise ValueError('invalid county sampling geometry')
        _CACHE[identity]=polygon
    return _CACHE[identity]


def sample_parent(parent,geometry,policy=SamplingPolicy()):
    if parent['count']<0 or not np.isfinite(parent['count']):raise ValueError('invalid real parent count')
    polygon=projected_geometry(parent['county_fips'],geometry);west,south,east,north=polygon.bounds
    rng=np.random.default_rng(stable_seed(parent['parent_id'],policy.seed));points=[]
    for _ in range(policy.max_attempt_batches):
        candidates=rng.uniform([west,south],[east,north],size=(max(32,policy.points_per_parent*4),2))
        for x,y in candidates:
            if polygon.covers(Point(x,y)):
                lon,lat=_INVERSE(x,y)
                # Projection roundtrip can move an exact edge; the source
                # polygon must also confirm containment before admission.
                if geometry.covers(Point(lon,lat)):points.append((lon,lat))
            if len(points)==policy.points_per_parent:break
        if len(points)==policy.points_per_parent:break
    if len(points)!=policy.points_per_parent:raise ValueError('bounded county sampling exhausted; geometry too thin or invalid')
    n=len(points);mass=np.full(n,parent['count']/n,dtype='float64');mass[-1]=parent['count']-mass[:-1].sum()
    return {'coordinates':np.asarray(points),'allocated_customers':mass,'training_weights':np.full(n,1/n),
        'area_km2':polygon.area/1e6,'seed':stable_seed(parent['parent_id'],policy.seed),'locations_simulated':True}
