"""Opt-in live-domain weather benchmark; no static/risk validation implied."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import resource
import time
import numpy as np
from raster_pipeline.contracts import GridSpec,SourceField,canonical
from raster_pipeline.decode import inspect_grib,tool_versions
from raster_pipeline.download import file_sha
from raster_pipeline.remap import CDORemapper
from raster_pipeline.weather import normalize_interval

p=argparse.ArgumentParser();p.add_argument('--geometry',required=True);p.add_argument('--inputs',required=True);p.add_argument('--output',required=True);args=p.parse_args()
root=Path(args.inputs);output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
started=time.monotonic();grid=GridSpec();geometry=Path(args.geometry);geometry_hash=file_sha(geometry)
remapper=CDORemapper(grid,output/'weights',geometry,geometry_hash)
fields=[];arrays=[]
for name in ['precipitation-0','precipitation-1','temperature','u','v']:
    path=root/(name+'.grib2');meta=inspect_grib(path)
    field=SourceField(**meta,url='https://opendata.dwd.de/weather/nwp/icon/grib/00/'+path.name,
        first_seen=datetime.now(timezone.utc),path=str(path),sha256=file_sha(path))
    fields.append(field.model_dump(mode='json'));arrays.append(remapper(field))
values,valid=normalize_interval(*arrays)
if not valid.all():raise ValueError('live domain has invalid weather cells')
record={'kind':'live-native-weather-single-interval','target_grid':grid.model_dump(),
    'tool_versions':tool_versions(),'geometry_sha256':geometry_hash,'geometry_bytes':geometry.stat().st_size,
    'peak_child_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    'weather_input_bytes':sum(Path(f['path']).stat().st_size for f in fields),
    'wall_seconds':time.monotonic()-started,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    'source_fields':fields,'remaps':remapper.records,'valid_cell_counts':valid.sum(axis=(1,2)).tolist(),
    'weather_band_min':values.min(axis=(1,2)).tolist(),'weather_band_max':values.max(axis=(1,2)).tolist(),
    'limitations':'One live weather interval across the default CONUS rectangle. Not a 72-hour complete batch or a static/risk-source benchmark.'}
(output/'benchmark.json').write_bytes(canonical(record));print(json.dumps(record,indent=2))
