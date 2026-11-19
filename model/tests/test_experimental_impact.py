import copy
from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient
import pytest

from service import experimental_impact as impact
from service.main import app


def payload(anchor=None):
    anchor = anchor or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return {'schema_version': 'experimental-impact-web/v1', 'model_id': impact.MODEL_ID,
            'fixture': False, 'experimental': True, 'physical_outage_probability': False,
            'training_weather': False, 'training_canopy': False, 'locations_simulated': True,
            'windows_are_fixed_utc_days': True, 'anchor_date': anchor.date().isoformat(),
            'published_at': datetime.now(timezone.utc).isoformat(), 'history_year': 2018,
            'calendar_extrapolation': True, 'limitations': 'Experimental impact, not outage probability.',
            'supported_counties': 1, 'unsupported_counties': ['06059'],
            'counties': {'06037': {'name': 'Los Angeles', 'state': 'CA', 'windows': [
                {'start': (anchor + timedelta(days=i)).isoformat(), 'end': (anchor + timedelta(days=i+1)).isoformat(),
                 'estimated_customer_impact': v, 'within_training_feature_ranges': True}
                for i, v in enumerate([0, 2.5, 10])]},
                '06059': {'name': 'Orange', 'state': 'CA', 'windows': []}}}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    path = tmp_path / 'latest.json'; data = payload(); path.write_text(json.dumps(data))
    monkeypatch.setenv('EXPERIMENTAL_IMPACT_PATH', str(path))
    monkeypatch.setenv('EXPERIMENTAL_IMPACT_ENABLED', 'true')
    cache = impact.ImpactCache(); monkeypatch.setattr(impact, 'cache', cache)
    return TestClient(app), data, path, cache


def test_fitted_model_impact_never_becomes_a_probability(setup):
    client, data, _, _ = setup
    r = client.get('/v1/forecasts/06037'); assert r.status_code == 200
    d = r.json()
    assert d['status'] == d['data_mode'] == 'experimental'
    assert d['experimental_impact']['model_id'] == impact.MODEL_ID
    assert d['experimental_impact']['weather_used'] is False
    assert d['experimental_impact']['fixed_utc_days'] is True
    for i, key in enumerate(['next_24h', 'next_24_48h', 'next_48_72h']):
        assert d[key]['probability'] is None
        assert d[key]['duration_low_hours'] is None
        assert d[key]['estimated_customer_impact'] == [0, 2.5, 10][i]
        assert datetime.fromisoformat(d[key]['window_start']) == datetime.fromisoformat(data['counties']['06037']['windows'][i]['start'])


def test_unsupported_county_is_not_filled_with_zero_or_transferred_estimate(setup):
    client, _, _, _ = setup
    for fips in ('06059', '99999'):
        d = client.get('/v1/forecasts/' + fips).json()
        assert d['status'] == 'unavailable'
        assert d['experimental_impact']['supported'] is False
        assert d['next_24h']['estimated_customer_impact'] is None
        assert d['next_24h']['probability'] is None


def test_coverage_and_map_share_the_actual_model_domain(setup):
    client, data, _, _ = setup
    assert client.get('/v1/experimental-impact').json() == data
    coverage = {c['county_fips']: c for c in client.get('/v1/coverage').json()}
    assert coverage['06037']['current_status'] == 'experimental'
    assert coverage['06059']['current_status'] == 'unavailable'
    assert coverage['06037']['gfs_coverage'] is False


def test_old_daily_batch_keeps_its_dates_and_is_marked_stale(setup):
    client, _, path, _ = setup
    old = payload(datetime(2020, 1, 1, tzinfo=timezone.utc)); path.write_text(json.dumps(old))
    d = client.get('/v1/forecasts/06037').json()
    assert d['is_stale'] is True
    assert d['next_24h']['window_start'].startswith('2020-01-01')


def test_refresh_failure_retains_last_good_batch_without_claiming_freshness(setup):
    client, _, path, cache = setup
    before = client.get('/v1/forecasts/06037').json()
    path.write_text('bad JSON'); cache.next_refresh = 0
    after = client.get('/v1/forecasts/06037').json()
    assert after['next_24h'] == before['next_24h']
    assert after['is_stale'] is True


def test_no_model_output_returns_service_error_without_synthetic_fallback(setup):
    client, _, path, _ = setup
    path.unlink()
    assert client.get('/v1/forecasts/06037').status_code == 503


@pytest.mark.parametrize('change', [
    {'fixture': True}, {'training_weather': True}, {'physical_outage_probability': True},
    {'model_id': 'other'}, {'supported_counties': 2}, {'unsupported_counties': []},
])
def test_invalid_provenance_or_coverage_is_rejected(change):
    data = payload(); data.update(change)
    with pytest.raises(ValueError): impact.validate(data)


def test_negative_values_and_missing_windows_are_rejected():
    for mutate in ('negative', 'missing'):
        data = copy.deepcopy(payload())
        if mutate == 'negative': data['counties']['06037']['windows'][0]['estimated_customer_impact'] = -1
        else: data['counties']['06037']['windows'].pop()
        with pytest.raises(ValueError): impact.validate(data)
