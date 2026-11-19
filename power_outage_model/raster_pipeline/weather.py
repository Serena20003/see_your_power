from pathlib import Path
import numpy as np
from .contracts import WeatherCube, NODATA


def precipitation_amount(before,after,*,tolerance=1e-4):
    if not np.isfinite(tolerance) or tolerance<0:raise ValueError('invalid precipitation precision bound')
    before=np.asarray(before,dtype='float64'); after=np.asarray(after,dtype='float64')
    delta=after-before
    if np.any(before[np.isfinite(before)]<0) or np.any(after[np.isfinite(after)]<0) or np.any(delta[np.isfinite(delta)] < -tolerance):
        raise ValueError('negative precipitation accumulation/difference (possible run reset)')
    return np.maximum(delta,0)


def normalize_interval(before,after,temperature,u,v,*,precipitation_tolerance=1e-4):
    arrays=[np.asarray(a,dtype='float64') for a in [before,after,temperature,u,v]]
    if len({a.shape for a in arrays})!=1: raise ValueError('weather grid mismatch')
    valid=np.logical_and.reduce([np.isfinite(a)&(a!=NODATA) for a in arrays])
    arrays=[np.where(valid,a,np.nan) for a in arrays]
    before,after,temperature,u,v=arrays
    data=np.stack([precipitation_amount(before,after,tolerance=precipitation_tolerance),temperature-273.15,np.hypot(u,v)]).astype('float32')
    return data,np.broadcast_to(valid,data.shape).copy()


def prepare_weather(inventory,grid,*,remapper,work_dir):
    root=Path(work_dir); root.mkdir(parents=True,exist_ok=True)
    intervals=inventory.selection.intervals
    shape=(len(intervals),3,*grid.shape)
    values=np.lib.format.open_memmap(root/'weather.npy',mode='w+',dtype='float32',shape=shape)
    valid=np.lib.format.open_memmap(root/'valid.npy',mode='w+',dtype='bool',shape=shape)
    fields={(f.run,f.field,f.end_step):f for f in inventory.fields}
    # Disk-backed per-field remaps bound RAM; precipitation is differenced in
    # float64 before the normalized interval is cast to float32.
    packing_errors={}
    def get(f):
        from .contracts import digest
        from .decode import inspect_grib, verify_metadata
        meta=inspect_grib(f.path);verify_metadata(f,meta)
        if f.field=='TOT_PREC':
            error=meta['metadata'].get('packingError')
            if error is None or not np.isfinite(error) or error<0:raise ValueError('decoded GRIB packing uncertainty required')
            packing_errors[(f.run,f.end_step)]=float(error)
        path=root/(digest({'sha':f.sha256,'grid':grid.model_dump(),'field':f.field})+'.npy')
        if not path.exists():
            a=remapper(f)
            if a.shape!=grid.shape:raise ValueError('remapped grid shape mismatch')
            np.save(path,np.asarray(a,dtype='float64'))
        return np.load(path,mmap_mode='r')
    provenance=[]
    for i,interval in enumerate(intervals):
        h=int((interval.start-interval.run).total_seconds()/3600)
        selected=[fields[(interval.run,'TOT_PREC',h)]]+[fields[(interval.run,f,h+1)] for f in ('TOT_PREC','T_2M','U_10M','V_10M')]
        arrays=[get(f) for f in selected]
        before_error=packing_errors[(interval.run,h)];after_error=packing_errors[(interval.run,h+1)]
        # Conservative remapping is a positive weighted average, so each input
        # bound is preserved. Subtraction adds the two GRIB quantization bounds.
        tolerance=max(1e-4,before_error+after_error)+1e-10
        data,mask=normalize_interval(*arrays,precipitation_tolerance=tolerance)
        values[i]=data; valid[i]=mask
        provenance.append({'start':interval.start.isoformat(),'end':interval.end.isoformat(),
            'run':interval.run.isoformat(),'sources':[f.model_dump(mode='json') for f in selected],
            'precipitation_precision_mm':{'before':before_error,'after':after_error,'negative_difference_tolerance':tolerance}})
    values.flush(); valid.flush()
    return WeatherCube(grid,values,intervals,valid,provenance)
