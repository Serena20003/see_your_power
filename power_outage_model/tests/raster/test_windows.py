from datetime import datetime,timezone,timedelta
import numpy as np
import pytest
from raster_pipeline.contracts import GridSpec,InputInterval,WeatherCube,HOUR
from raster_pipeline.windows import iter_windows,target_intervals

T=datetime(2026,12,31,23,tzinfo=timezone.utc)
G=GridSpec(grid_id='tiny',west=-120,south=34,east=-119,north=35,resolution=1)

def cube(start=-24,stop=72,missing=()):
    hours=[h for h in range(start,stop) if h not in missing]
    intervals=[InputInterval(start=T+h*HOUR,end=T+(h+1)*HOUR,run=T-24*HOUR) for h in hours]
    values=np.array([np.full((3,1,1),h) for h in hours])
    return WeatherCube(G,values,intervals,np.ones_like(values,dtype=bool),[{'hour':h} for h in hours])

@pytest.mark.parametrize('lookback',[0,5,24])
def test_exact_72_windows_include_target_and_year_rollover(lookback):
    windows=list(iter_windows(cube(),target_intervals(T),lookback))
    assert len(windows)==72 and windows[0].values.shape==(lookback+1,3,1,1)
    assert windows[0].values[:,0,0,0].tolist()==list(range(-lookback,1))
    assert windows[-1].target.end==datetime(2027,1,3,23,tzinfo=timezone.utc)
    assert windows[0].provenance[-1]=={'hour':0}

def test_missing_history_middle_and_horizon_are_explicit():
    windows=list(iter_windows(cube(start=0,stop=71,missing=(20,)),target_intervals(T),5))
    assert not windows[0].valid.all()
    assert not windows[20].valid.all()
    assert windows[26].valid.all()
    assert not windows[-1].valid.all()
    assert np.isnan(windows[0].values[0]).all()

def test_duplicate_interval_and_misaligned_targets_fail():
    c=cube(); c.intervals[1]=c.intervals[0]
    with pytest.raises(ValueError): list(iter_windows(c,target_intervals(T),5))
    with pytest.raises(ValueError): target_intervals(T+timedelta(minutes=1))
