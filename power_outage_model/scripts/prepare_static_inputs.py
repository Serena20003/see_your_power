"""Prepare pinned live static inputs from the locally downloaded source archives.

Run from power_outage_model with .venv312/bin/python scripts/prepare_static_inputs.py.
Produces .cache/static-prepared/static-live.json and provenance; does not download
or substitute data, assert transmission assessment, or publish a forecast.
"""
import io,json,zipfile,hashlib,logging
from pathlib import Path
import shapefile,shapely,rasterio
from shapely.geometry import shape,mapping,box
from shapely.ops import unary_union,transform
from pyproj import Transformer

logging.getLogger('shapefile').setLevel(logging.ERROR)
root=Path(__file__).resolve().parents[1];sources=root/'.cache/static-inputs';out=root/'.cache/static-prepared';out.mkdir(parents=True,exist_ok=True)

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
def reader(z,base):
 return shapefile.Reader(shp=io.BytesIO(z.read(base+'.shp')),dbf=io.BytesIO(z.read(base+'.dbf')),shx=io.BytesIO(z.read(base+'.shx')))
def write(name,geoms,properties=None):
 p=out/name;p.write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':properties or {},'geometry':mapping(g)} for g in geoms]},separators=(',',':')));return p

with zipfile.ZipFile(sources/'tl_2026_us_state.zip') as z:
 r=reader(z,next(n[:-4] for n in z.namelist() if n.endswith('.shp')))
 states={s.record.as_dict()['STUSPS']:shapely.make_valid(shape(s.shape.__geo_interface__)) for s in r.iterShapeRecords()}
conus=unary_union([g for k,g in states.items() if k not in ['AK','HI','PR','VI','GU','MP','AS']])
ca=write('california.geojson',[states['CA']],{'source':'TIGER2026'})
print('Administrative boundaries prepared',flush=True)
land=None;extent=box(-125,24,-66,50)
with zipfile.ZipFile(sources/'gshhg-shp-2.3.7.zip') as z:
 for level in (1,2,3,4):
  base=f'GSHHS_shp/f/GSHHS_f_L{level}'
  clipped=[]
  for s in reader(z,base).iterShapes(bbox=(-125,24,-66,50)):
   g=shapely.make_valid(shape(s.__geo_interface__)).intersection(extent)
   if not g.is_empty:clipped.append(g)
  g=unary_union(clipped)
  land=g if land is None else land.difference(g) if level%2==0 else land.union(g)
  print('Shoreline hierarchy level',level,'processed',len(clipped),flush=True)
land=shapely.make_valid(land.intersection(conus))
landpath=write('analysis-land.geojson',[land],{'recipe':'TIGER2026 lower 48 + DC intersect GSHHG2.3.7 full resolution (L1-L2+L3-L4)'})
# No reviewed asset assessment was supplied; preserve unassessed coverage.
assessed=write('assessed-empty.geojson',[],{})
raw=json.loads((sources/'cec-transmission-lines.geojson').read_text());project=Transformer.from_crs(3857,4326,always_xy=True).transform
raw.pop('crs',None)
for f in raw['features']:f['geometry']=mapping(transform(project,shape(f['geometry'])))
trans=out/'cec-transmission-wgs84.geojson';trans.write_text(json.dumps(raw,separators=(',',':')))
config=json.loads((root/'raster_pipeline/configs/static-v1.json').read_text())
paths={'canopy':sources/'nlcd-tcc-2025-v2025-6/nlcd_tcc_conus_wgs84_v2025-6_20250101_20251231.tif','analysis_land':landpath,'california':ca,'assessed':assessed,'transmission':trans}
for name,p in paths.items():
 config['sources'][name].update(path=str(p),sha256=sha(p))
with rasterio.open(paths['canopy']) as src:config['sources']['canopy']['crs']=src.crs.to_string()
config['sources']['analysis_land'].update(uri='https://www.soest.hawaii.edu/pwessel/gshhg/',vintage='TIGER2026 states + GSHHG 2.3.7 full resolution L1-L2+L3-L4',attribution='US Census Bureau; Wessel and Smith GSHHG',license='Census public domain; GSHHG LGPL-3.0-or-later')
config['sources']['california'].update(uri='https://www2.census.gov/geo/tiger/TIGER2026/STATE/tl_2026_us_state.zip',vintage='TIGER2026 California state boundary')
config['sources']['assessed'].update(uri='local:explicitly-unassessed',vintage='No independently reviewed transmission assessment footprint supplied; empty geometry means unassessed, not zero exposure',license='Project-generated metadata',attribution='See Your Power: no asset coverage claim')
config['sources']['transmission']['vintage']='CEC pinned 2026-11-17 export, EPSG:3857 transformed to EPSG:4326'
(out/'static-live.json').write_text(json.dumps(config,indent=2))
recipe={'method':'TIGER2026 CONUS intersect GSHHG 2.3.7 full resolution L1-L2+L3-L4; no geometry simplification','source_archives':{n:sha(sources/n) for n in ['tl_2026_us_state.zip','gshhg-shp-2.3.7.zip','cec-transmission-lines.geojson']},'geometry_crs':'EPSG:4326','asset_assessment':'unassessed; empty footprint','prepared_inputs':{k:{'path':str(v),'sha256':sha(v)} for k,v in paths.items()}}
(out/'preparation.json').write_text(json.dumps(recipe,indent=2))
print('Pinned static config ready:',out/'static-live.json',flush=True)
