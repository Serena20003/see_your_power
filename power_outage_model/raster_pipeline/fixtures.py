"""Explicit CC0 test data. This module is never selected as a live fallback."""
from datetime import datetime,timezone
import json
from pathlib import Path
import numpy as np
from .contracts import HOUR,SourceField,InputInventory,canonical
from .config import PipelineConfig
from .decode import inspect_grib,verify_metadata,tool_versions
from .download import file_sha


def generate_fixture(root,cutoff,grid,*,missing_hour=None):
    import eccodes as e
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    run=cutoff-6*HOUR;records=[]
    # One source grid encloses the two target cells with exact centers.
    for h in range(79):
        if missing_hour is not None and h==missing_hour+7:continue
        for field,param in [('TOT_PREC',228228),('T_2M',167),('U_10M',165),('V_10M',166)]:
            g=e.codes_grib_new_from_samples('regular_ll_sfc_grib2')
            try:
                for key,value in [('Ni',4),('Nj',3),('latitudeOfFirstGridPointInDegrees',35.5),
                    ('latitudeOfLastGridPointInDegrees',33.5),('longitudeOfFirstGridPointInDegrees',239.5),
                    ('longitudeOfLastGridPointInDegrees',242.5),('iDirectionIncrementInDegrees',1),('jDirectionIncrementInDegrees',1),
                    ('jScansPositively',0),('paramId',param),('dataDate',int(run.strftime('%Y%m%d'))),('dataTime',run.hour*100)]:
                    e.codes_set(g,key,value)
                if field=='TOT_PREC':
                    e.codes_set(g,'stepType','accum');e.codes_set(g,'startStep',0);e.codes_set(g,'endStep',h)
                    value=5.5*h
                else:
                    e.codes_set(g,'forecastTime',h)
                    value={'T_2M':310.65,'U_10M':10.5,'V_10M':14}[field]
                e.codes_set_values(g,np.full(12,value))
                path=root/f'{field}-{h:03d}.grib2'
                with path.open('wb') as f:e.codes_write(g,f)
            finally:e.codes_release(g)
            meta=inspect_grib(path)
            f=SourceField(**meta,url=f'fixture://{path.name}',first_seen=cutoff,sha256=file_sha(path),path=path.name)
            records.append(f.model_dump(mode='json'))
    (root/'catalog.json').write_bytes(canonical(records))
    (root/'config.json').write_bytes(canonical({'grid':grid.model_dump(),'lookback_hours':5,'catalog':records}))


def load_fixture_config(root):
    return PipelineConfig.model_validate_json((Path(root)/'config.json').read_bytes())


def fixture_inventory(selection,root):
    fields=[]
    for f in selection.fields:
        path=Path(root)/(f.path or f.url.rsplit('/',1)[-1])
        if not path.resolve().is_relative_to(Path(root).resolve()):raise ValueError('fixture escapes root')
        if file_sha(path)!=f.sha256:raise ValueError('fixture checksum mismatch')
        verify_metadata(f,inspect_grib(path))
        fields.append(f.model_copy(update={'path':str(path),'retrieved_at':selection.cutoff}))
    return InputInventory(selection=selection,fields=fields,tool_versions=tool_versions())


def fixture_remap(field,grid):
    # Explicit fixture path accepts only exact-center regular_ll sampling;
    # native live ICON data always goes through CDO.
    import eccodes as e
    with open(field.path,'rb') as src:
        g=e.codes_grib_new_from_file(src)
        try:
            if e.codes_get(g,'gridType')!='regular_ll':raise ValueError('fixture requires regular latlon grid')
            lats=e.codes_get_array(g,'latitudes');lons=(e.codes_get_array(g,'longitudes')+180)%360-180
            vals=e.codes_get_values(g);out=np.empty(grid.shape,dtype='float64')
            for r in range(grid.height):
                for c in range(grid.width):
                    x,y=grid.center(r,c);index=np.flatnonzero(np.isclose(lons,x)&np.isclose(lats,y))
                    if len(index)!=1:raise ValueError('fixture grid has no exact matching source center')
                    out[r,c]=vals[index[0]]
            return out
        finally:e.codes_release(g)
