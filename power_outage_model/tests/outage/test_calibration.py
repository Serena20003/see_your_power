import numpy as np
import pytest
from scipy.special import expit
from outage_model.calibration import fit_sigmoid, calibrate, fit_calibration
from outage_model.contracts import DataReadinessError
from outage_model.splits import make_splits
from .helpers import dataset


def test_nonnegative_slope_and_regional_shrinkage():
    rng=np.random.default_rng(42);f=rng.normal(size=6000);r=np.array(['a']*3000+['b']*3000)
    y=(rng.random(6000)<expit(.8*f+np.where(r=='a',.8,-.8))).astype(int)
    blocks=np.array([str(i%30) for i in range(6000)])
    a=fit_sigmoid(f,y,r,blocks,penalty=1,min_positive=100,min_negative=100,min_blocks=20)
    b=fit_sigmoid(f,y,r,blocks,penalty=1000,min_positive=100,min_negative=100,min_blocks=20)
    assert a.slope>=0 and sum(a.offsets.values())==pytest.approx(0,abs=1e-8)
    assert sum(x*x for x in b.offsets.values())<sum(x*x for x in a.offsets.values())
    assert calibrate(a,0,'unknown')['regional_offset']==0


def test_calibration_degenerate_fails():
    with pytest.raises(DataReadinessError):fit_sigmoid(np.zeros(10),np.ones(10),['a']*10,['b']*10)


def test_test_labels_do_not_change_calibration():
    d,p=dataset(60);s=make_splits(d,p); f=d.features.values[:,0].astype(float)
    a=fit_calibration(f,d.labels,[r['region'] for r in d.rows],s,{'dataset':d,'test_mode':True})
    y=d.labels.copy()
    for i,k in enumerate(d.features.keys):
        if s.assignments[k.id]=='test':y[i]=1-y[i]
    b=fit_calibration(f,y,[r['region'] for r in d.rows],s,{'dataset':d,'test_mode':True})
    assert a==b
