"""One-message GRIB decoding; ecCodes validates actual metadata/packing."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import numpy as np
from .source import validate_field


def tool_versions():
    import eccodes, rasterio
    try: cdo=subprocess.run(['cdo','-V'],capture_output=True,text=True,check=True).stderr.splitlines()[0]
    except (OSError, subprocess.CalledProcessError, IndexError): cdo='unavailable'
    return {'eccodes':eccodes.codes_get_api_version(),'rasterio':rasterio.__version__,
        'gdal':rasterio.__gdal_version__,'cdo':cdo}


def inspect_grib(path):
    import eccodes as e
    with open(path,'rb') as f:
        g=e.codes_grib_new_from_file(f)
        if g is None: raise ValueError('empty/non-GRIB input')
        try:
            def get(key,default=None):
                try:return e.codes_get(g,key)
                except e.CodesInternalError:return default
            short=get('shortName')
            field={'tp':'TOT_PREC','tot_prec':'TOT_PREC','2t':'T_2M','t_2m':'T_2M',
                   '10u':'U_10M','u_10m':'U_10M','10v':'V_10M','v_10m':'V_10M'}.get(short)
            if not field: raise ValueError(f'unsupported source field {short}')
            # Normalize the *unit*, never the weather cadence. DWD encodes
            # its zero-lead precipitation boundary in minutes (0m).
            try:
                e.codes_set(g,'stepUnits',1)
                start_step=int(get('startStep'));end_step=int(get('endStep'))
            except (TypeError,ValueError,e.CodesInternalError):raise ValueError('non-hour-aligned GRIB step bounds')
            if get('stepType')!=('accum' if field=='TOT_PREC' else 'instant'):
                raise ValueError('unexpected GRIB temporal processing')
            vector='earth'
            if field in ('U_10M','V_10M'):
                vector=wind_reference({k:get(k) for k in ('uvRelativeToGrid','centre','paramId','gridType','uuidOfHGrid')})
            md={k:get(k) for k in ['gridType','uuidOfHGrid','md5GridSection','numberOfDataPoints',
                'Ni','Nj','iScansNegatively','jScansPositively','packingType','shortName','paramId','name']}
            run=datetime.strptime(f"{get('dataDate'):08d}{get('dataTime'):04d}",'%Y%m%d%H%M').replace(tzinfo=timezone.utc)
            result={'field':field,'run':run,'start_step':start_step,'end_step':end_step,
                'units':get('units'),'level':get('level'),'level_type':get('typeOfLevel'),
                'grid_id':get('md5GridSection'),'packing':get('packingType'),
                'vector_reference':vector,'metadata':md}
        finally:e.codes_release(g)
        second=e.codes_grib_new_from_file(f)
        if second is not None:
            e.codes_release(second); raise ValueError('expected one GRIB message per input')
    return result


def verify_metadata(expected, actual):
    for key in ['field','run','start_step','end_step','units','level','level_type','grid_id','packing','vector_reference']:
        if getattr(expected,key)!=actual[key]: raise ValueError(f'GRIB metadata mismatch: {key}')
    validate_field(expected)


def read_values(path):
    import eccodes as e
    with open(path,'rb') as f:
        g=e.codes_grib_new_from_file(f)
        if g is None:raise ValueError('missing GRIB message')
        try:
            values=e.codes_get_values(g).astype('float64')
            missing=e.codes_get(g,'missingValue')
            values[values==missing]=np.nan
            if e.codes_get(g,'bitmapPresent'):
                values[np.asarray(e.codes_get_array(g,'bitmap'))==0]=np.nan
            return values
        finally:e.codes_release(g)


def wind_reference(metadata):
    flag=metadata.get('uvRelativeToGrid')
    if flag==0:return 'earth'
    # DWD ICON database: U_10M is zonal, V_10M meridional. Template 101
    # has no resolution/component flag. Permit only this verified product;
    # another native grid or producer requires an explicit adapter update.
    if flag is None and metadata.get('centre')=='edzw' and metadata.get('paramId') in (165,166) and metadata.get('gridType')=='unstructured_grid' and metadata.get('uuidOfHGrid')=='a27b8de618c411e4820ab5b098c6a5c0':
        return 'earth'
    raise ValueError('unsupported/unknown wind vector orientation')
