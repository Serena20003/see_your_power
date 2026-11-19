from fastapi.testclient import TestClient
import pytest
from service import main


@pytest.fixture
def client(monkeypatch, tmp_path):
    dist = tmp_path / 'dist'; (dist / 'data').mkdir(parents=True)
    (dist / 'index.html').write_text('<html>Runway app</html>')
    (dist / 'data/counties.geojson').write_text('{"type":"FeatureCollection","features":[]}')
    (tmp_path / 'private.txt').write_text('private source file')
    monkeypatch.setattr(main, '_DIST_DIR', dist)
    return TestClient(main.app)


def test_public_county_data_is_not_replaced_by_spa_html(client):
    r = client.get('/data/counties.geojson')
    assert r.status_code == 200
    assert r.json() == {'type': 'FeatureCollection', 'features': []}


def test_client_side_route_still_gets_the_spa(client):
    r = client.get('/model-sources')
    assert r.status_code == 200
    assert '<html>Runway app</html>' in r.text


def test_missing_static_file_is_a_404_not_html(client):
    r = client.get('/data/missing.geojson')
    assert r.status_code == 404
    assert r.json()['detail'] == 'Static file not found'


def test_unknown_api_path_remains_json_404(client):
    r = client.get('/v1/not-a-route')
    assert r.status_code == 404
    assert r.headers['content-type'] == 'application/json'


def test_public_file_request_cannot_escape_dist(client):
    r = client.get('/data/%2e%2e/%2e%2e/private.txt')
    assert r.status_code == 404
    assert 'private source file' not in r.text
