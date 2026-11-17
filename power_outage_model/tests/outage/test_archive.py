import json
from datetime import datetime, timezone
import pytest
from raster_pipeline.run import run_fixture
from raster_pipeline.storage import LocalStore
from outage_model.archive import archive_batch, load_archive

@pytest.fixture
def upstream(tmp_path):
    store=LocalStore(tmp_path)
    m=run_fixture(datetime(2026,11,16,12,0,tzinfo=timezone.utc),store)
    return store,m,f"raster/v1/runs/{m['batch_id']}/manifest.json"


def test_archive_survives_upstream_deletion(upstream):
    store,m,key=upstream
    ref=archive_batch(store,key,test_mode=True)
    for k in store.keys('raster/v1'): store.delete(k)
    batch=load_archive(store,ref,test_mode=True)
    assert len(batch.manifest['intervals'])==72
    assert batch.ref.fixture
    assert batch.weather.shape[:2]==(72,3)


def test_archive_fixture_live_and_corruption(upstream):
    store,m,key=upstream
    with pytest.raises(ValueError,match='fixture'): archive_batch(store,key)
    obj=m['intervals'][0]['weather']
    store.path(obj['key']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'): archive_batch(store,key,test_mode=True)
    assert not store.keys('outage/v1/archive/manifests')
