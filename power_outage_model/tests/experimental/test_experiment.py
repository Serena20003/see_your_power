from datetime import date
import io
import numpy as np
import pytest
from shapely.geometry import box,Point
from outage_model.experimental.contracts import CountyDay,SamplingPolicy
from outage_model.experimental.history import aggregate_rows
from outage_model.experimental.sampling import sample_parent,stable_seed
from outage_model.experimental.dataset import role_for_anchor,make_parents


def test_missing_is_not_zero_and_real_count_is_preserved():
    rows=[{'fips_code':'01001','customers_out':'','run_start_time':'2018-01-01 00:00:00'},
          {'fips_code':'01001','customers_out':'5','run_start_time':'2018-01-01 00:15:00'},
          {'fips_code':'01001','customers_out':'8','run_start_time':'2018-01-01 00:30:00'},
          {'fips_code':'01003','customers_out':'','run_start_time':'2018-01-01 00:00:00'}]
    days,audit=aggregate_rows(rows)
    assert len(days)==1 and days[0].reported_peak_customers==8 and days[0].received_records==2
    assert audit['missing_count_rows']==2


def test_points_are_bounded_reproducible_and_conserve_mass():
    parent={'parent_id':'p','county_fips':'01001','anchor_date':'2018-01-01','horizon':0,'count':103.,'role':'train'}
    geometry=box(-87,32,-86,33);policy=SamplingPolicy(points_per_parent=8)
    a=sample_parent(parent,geometry,policy);b=sample_parent(parent,geometry,policy)
    np.testing.assert_array_equal(a['coordinates'],b['coordinates'])
    assert len(a['coordinates'])==8
    assert all(geometry.covers(Point(x,y)) for x,y in a['coordinates'])
    assert a['allocated_customers'].sum()==pytest.approx(103)
    assert a['training_weights'].sum()==pytest.approx(1)
    assert stable_seed('other',42)!=stable_seed('p',42)
    with pytest.raises(ValueError):SamplingPolicy(points_per_parent=33)


def test_parent_splits_have_temporal_embargo():
    assert role_for_anchor(date(2018,8,31)) is None
    assert role_for_anchor(date(2018,9,2)) is None
    assert role_for_anchor(date(2018,9,4))=='tune'
    assert role_for_anchor(date(2018,11,4))=='test'


def test_missing_horizon_is_excluded_instead_of_imputed_zero():
    days=[CountyDay(county_fips='01001',day=date(2018,1,1),reported_peak_customers=5,received_records=1)]
    parents,stats=make_parents(days,{'01001':box(-87,32,-86,33)})
    assert all(p['count']==5 for p in parents)
    assert not any(p['horizon'] in (1,2) for p in parents)


def test_nonoverlapping_anchors_cover_weekdays():
    from datetime import timedelta
    days=[CountyDay(county_fips='01001',day=date(2018,1,1)+timedelta(days=i),reported_peak_customers=5,received_records=1) for i in range(60)]
    parents,_=make_parents(days,{'01001':box(-87,32,-86,33)})
    anchors=sorted({date.fromisoformat(p['anchor_date']) for p in parents})
    assert len({a.weekday() for a in anchors})==7
    assert len({p['target_date'] for p in parents})==len(parents)
