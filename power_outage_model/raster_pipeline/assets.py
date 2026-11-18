"""CEC transmission route exposure; no asset-failure interpretation."""
import numpy as np
from pyproj import Transformer
from shapely.ops import transform,unary_union
from shapely.geometry import shape


def transmission_exposure(features,cells,pilot,assessed):
    project=Transformer.from_crs(4326,3310,always_xy=True).transform
    inventory=[]; groups={'overhead':[],'underground':[],'unknown':[]}
    seen={}
    for feature in features:
        p=feature.get('properties',{})
        status=str(p.get('Status',p.get('STATUS','unknown'))).strip().lower()
        kind={'OH':'overhead','UG':'underground'}.get(str(p.get('Type',p.get('TYPE',''))).strip().upper(),'unknown')
        geom=feature['geometry'];geom=geom if hasattr(geom,'geom_type') else shape(geom)
        if geom.geom_type not in ('LineString','MultiLineString') or not geom.is_valid:raise ValueError('invalid transmission line geometry')
        ident=str(feature.get('id',p.get('GlobalID',geom.normalize().wkb_hex)))
        if ident in seen:
            if not seen[ident].equals(geom):raise ValueError('duplicate source ID with conflicting geometry')
            continue
        seen[ident]=geom
        circuits={'single':1,'double':2,'triple':3}.get(str(p.get('Circuit','')).lower())
        inventory.append({'source_id':ident,'status':status,'recognized_status':status in ('operational','proposed','closed','unknown'),
            'type':kind,'raw_type':p.get('Type'),'owner':p.get('Owner'),'voltage_class':p.get('kV'),
            'circuit_count':circuits,'properties':p})
        if status!='operational':continue
        clipped=geom.intersection(pilot).intersection(assessed)
        if not clipped.is_empty:groups[kind].append(clipped)
    # Remove all coincident route portions globally. In a conflict, retain the
    # first recognized type (OH, then UG); preserve raw attributes for inspection.
    dedup={};occupied=None
    for kind,lines in groups.items():
        merged=unary_union(lines)
        if occupied is not None:merged=merged.difference(occupied)
        dedup[kind]=merged
        occupied=merged if occupied is None else occupied.union(merged)
    result=np.full((4,len(cells)),-9999.,dtype='float32');coverage=np.zeros(len(cells),dtype='uint8')
    for i,cell in enumerate(cells):
        footprint=cell.intersection(pilot)
        if footprint.is_empty or footprint.area==0:continue
        if not assessed.covers(footprint):coverage[i]=2;continue
        coverage[i]=1
        lengths=[transform(project,geom.intersection(footprint)).length/1000 for geom in dedup.values()]
        result[:,i]=[sum(lengths),*lengths]
    return result,coverage,inventory
