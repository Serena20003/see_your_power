import numpy as np
import pytest
from raster_pipeline.contracts import StaticContext
from raster_pipeline.risk import RiskCalculator,RiskConfig
from test_windows import G,cube,T
from raster_pipeline.windows import iter_windows,target_intervals


def context(canopy=0):
    return StaticContext(G,'test',np.full((1,1),canopy),np.ones((1,1)),np.ones((1,1),bool),np.full((4,1,1),-9999.),np.ones((3,1,1),dtype='uint8'))

def window(rain=5.5,temp=37.5,wind=17.5):
    c=cube();c.values=c.values.astype(float);c.values[:]=np.array([rain,temp,wind])[None,:,None,None]
    return next(iter_windows(c,target_intervals(T),5))

@pytest.mark.parametrize('canopy,want',[(0,.5),(1,.55)])
def test_midpoint_score_and_diagnostics(canopy,want):
    r=RiskCalculator().calculate(window(),context(canopy),RiskConfig())
    assert r.score[0,0]==pytest.approx(want)
    assert r.diagnostics['cumulative_precipitation_mm'][0,0]==33
    assert r.metadata['value_kind']=='heuristic_score'
    assert r.metadata['calibrated'] is False and r.metadata['asset_conditioned'] is False

@pytest.mark.parametrize('rain,temp,wind,want',[(0,20,0,0),(10,45,25,1),(0,-15,0,.2),(100,90,100,1)])
def test_ramps_cold_and_saturation(rain,temp,wind,want):
    assert RiskCalculator().calculate(window(rain,temp,wind),context(1),RiskConfig()).score[0,0]==pytest.approx(want)


def test_nodata_and_california_inventory_independence():
    s=context(1);calc=RiskCalculator(); w=window()
    first=calc.calculate(w,s,RiskConfig()).score.copy()
    s.exposure[:]=0;s.coverage[2]=0
    np.testing.assert_equal(calc.calculate(w,s,RiskConfig()).score,first)
    s.valid_land_fraction[:]=.94
    assert calc.calculate(w,s,RiskConfig()).score[0,0]==-9999
    s.valid_land_fraction[:]=1; w.valid[0,0]=False
    assert not calc.calculate(w,s,RiskConfig()).valid.any()

@pytest.mark.parametrize('kw',[{'weights':[-.1,.5,.6]},{'weights':[.2,.2,.2]},{'wind':[25,10]},{'rain':[1,1]},{'canopy_weight':-1}])
def test_invalid_configs_rejected(kw):
    with pytest.raises(ValueError):RiskConfig(**kw)
