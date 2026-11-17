"""Discovery from an observed metadata catalog, never nominal filename horizons.

Catalogs come from inspect-source, which downloads and decodes real GRIB messages.
First-seen timestamps are observations; HTTP Last-Modified is not availability.
"""
from datetime import datetime
from .contracts import HOUR, FIELDS, InputInterval, SourceSelection, utc


def ceil_hour(value: datetime):
    value=utc(value)
    floor=value.replace(minute=0,second=0,microsecond=0)
    return floor if value==floor else floor+HOUR


def validate_field(f):
    expected={'TOT_PREC':({'kg m-2','kg m**-2','mm'},0,'surface'),
              'T_2M':({'K'},2,'heightAboveGround'),
              'U_10M':({'m s-1','m s**-1'},10,'heightAboveGround'),
              'V_10M':({'m s-1','m s**-1'},10,'heightAboveGround')}
    units,level,kind=expected[f.field]
    if f.units not in units or f.level!=level or f.level_type!=kind:
        raise ValueError(f'{f.field}: unsupported units/level {f.units}/{f.level_type}/{f.level}')
    if f.vector_reference!='earth': raise ValueError('unsupported grid-relative wind vectors')
    if not f.grid_id or not f.packing: raise ValueError('missing native grid/packing identity')
    if f.field=='TOT_PREC' and f.start_step!=0:
        raise ValueError('only cumulative precipitation from initialization is supported')
    if f.field!='TOT_PREC' and f.start_step!=f.end_step:
        raise ValueError('temperature/wind must be instantaneous endpoint samples')


def discover_run(cutoff, target_start, config):
    cutoff, target_start=utc(cutoff),utc(target_start)
    if target_start!=ceil_hour(cutoff): raise ValueError('target start must equal ceil(cutoff)')
    by_run={}
    for f in config.catalog:
        if f.first_seen>cutoff or f.run>cutoff: continue
        validate_field(f)
        key=(f.field,f.end_step)
        run=by_run.setdefault(f.run,{})
        if key in run and run[key]!=f: raise ValueError('ambiguous source metadata')
        run[key]=f

    def records(run,start):
        lead=(start-run)/HOUR
        if lead<0 or not float(lead).is_integer(): return None
        h=int(lead)
        keys=[('TOT_PREC',h)]+[(f,h+1) for f in FIELDS]
        fields=by_run[run]
        if not all(k in fields for k in keys): return None
        result=[fields[k] for k in keys]
        if len({f.grid_id for f in result})!=1: raise ValueError('mixed source grid')
        return result

    runs=sorted(by_run,reverse=True)
    selected=next((r for r in runs if all(records(r,target_start+h*HOUR) for h in range(72))),None)
    if selected is None: raise ValueError('no eligible run covers all 72 hourly intervals by cutoff')
    intervals=[]; chosen={}; missing=[]
    for h in range(-config.lookback_hours,72):
        start=target_start+h*HOUR
        candidates=[selected] if h>=0 else [r for r in runs if r<=selected]
        found=next(((r,records(r,start)) for r in candidates if records(r,start)),None)
        if found is None:
            missing.append(start); continue
        run,fields=found
        intervals.append(InputInterval(start=start,end=start+HOUR,run=run))
        for f in fields: chosen[(f.run,f.field,f.end_step)]=f
    return SourceSelection(cutoff=cutoff,target_start=target_start,selected_run=selected,
        intervals=intervals,fields=list(chosen.values()),missing_history=missing)
