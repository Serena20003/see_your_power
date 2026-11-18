import numpy as np
from outage_model.evaluate import metrics, block_skill_interval, evaluate_scope, load_release_config


def test_small_or_one_class_scope_cannot_pass():
    config=load_release_config()
    rows=[{'block_id':'b','domain_id':'u','start':'2026-01-01T00:00:00Z','state':'CA','region':'West','season':'Q1'}]*20
    result=evaluate_scope(np.ones(20),np.full(20,.9),{'seasonal':np.full(20,.5),'logistic':np.full(20,.5)},rows,config)
    assert not result['passed'] and 'minimum_evidence' in result['failures']


def test_bootstrap_resamples_blocks_and_reports_degenerate():
    y=np.array([0,1]*100);p=np.where(y,.9,.1);b=np.full(len(y),.5)
    lower,upper=block_skill_interval(y,p,b,[str(i//10) for i in range(len(y))],[f'u{i%2}' for i in range(len(y))],replicates=100)
    assert 0<lower<=upper<=1
    assert block_skill_interval(y,p,b,['one']*len(y),['u']*len(y)) is None


def test_metrics_include_prevalence_and_reliability_counts():
    y=np.array([0,1]*1000);p=np.where(y,.8,.2)
    m=metrics(y,p)
    assert m['prevalence']==.5 and len(m['reliability'])==10
    assert all(b['count']==200 for b in m['reliability'])


def test_sparse_geography_cannot_hide_under_pooled_scope():
    config=load_release_config();config.update(minimum_positive=2,minimum_negative=2,minimum_blocks=2,minimum_days=2,
        minimum_bin_rows=1,bootstrap_replicates=20,mean_absolute_calibration_gap=.2,maximum_calibration_gap=.2)
    y=np.array([0,1]*20);p=np.where(y,.95,.05)
    rows=[{'block_id':str(i),'domain_id':'u','start':f'2026-01-{i%28+1:02d}T00:00:00Z',
        'state':'OR' if i==0 else 'CA','region':'West','season':'Q1','wind_severity':'0',
        'rain_severity':'0','temperature_severity':'0','canopy_slice':'0','spatial_boundary':'False'} for i in range(40)]
    result=evaluate_scope(y,p,{'seasonal':np.full(40,.5),'logistic':np.full(40,.5)},rows,config)
    assert not result['passed']
    assert 'unsupported_scope_slice:state:OR' in result['failures']
