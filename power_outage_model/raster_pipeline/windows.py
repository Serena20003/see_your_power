import numpy as np
from .contracts import HOUR, InputInterval, WeatherWindow, utc


def target_intervals(start):
    start=utc(start)
    if start.minute or start.second or start.microsecond:raise ValueError('target hour alignment required')
    return [InputInterval(start=start+h*HOUR,end=start+(h+1)*HOUR,run=start) for h in range(72)]


def iter_windows(cube,target_intervals,lookback_hours):
    if not 0<=lookback_hours<=24:raise ValueError('lookback must be 0–24')
    keys={}
    if cube.values.shape!=(len(cube.intervals),3,*cube.grid.shape) or cube.valid.shape!=cube.values.shape:
        raise ValueError('invalid cube dimensions')
    for i,v in enumerate(cube.intervals):
        if v.end-v.start!=HOUR or v.start.minute or v.start.second or v.start.microsecond:
            raise ValueError('cube intervals must be actual UTC hours')
        if v.start in keys:raise ValueError('duplicate interval')
        keys[v.start]=i
    for target in target_intervals:
        if target.end-target.start!=HOUR:raise ValueError('target interval must be an hour')
        values=np.full((lookback_hours+1,3,*cube.grid.shape),np.nan,dtype='float32')
        valid=np.zeros_like(values,dtype=bool); intervals=[];provenance=[]
        for j,h in enumerate(range(-lookback_hours,1)):
            i=keys.get(target.start+h*HOUR)
            intervals.append(cube.intervals[i] if i is not None else None)
            if i is not None:
                values[j]=cube.values[i];valid[j]=cube.valid[i]
                provenance.append(cube.provenance[i] if cube.provenance else {'run':cube.intervals[i].run.isoformat()})
            else:provenance.append({'missing':(target.start+h*HOUR).isoformat()})
        yield WeatherWindow(target,values,valid,intervals,provenance)
