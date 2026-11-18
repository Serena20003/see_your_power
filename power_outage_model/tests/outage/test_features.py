from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import numpy as np
import pytest
from outage_model.features import build_features, load_registry, neighborhood, render_feature_docs
from outage_model.archive import archive_batch, load_archive
from .test_archive import upstream


def test_neighborhood_uses_land_area_and_missing_propagates():
    values=np.arange(9,dtype=float).reshape(3,3); area=np.ones((3,3)); area[0,0]=10
    mask=np.ones((3,3),bool); mask[2,2]=False
    valid=mask.copy()
    peak,mean=neighborhood(values,area,mask,valid)
    assert peak[1,1]==7
    assert mean[1,1]==pytest.approx(sum(range(8))/17)
    valid[0,0]=False
    assert np.isnan(neighborhood(values,area,mask,valid)[1][1,1])


def test_windows_and_registry_docs(upstream):
    store,m,key=upstream
    batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    table=build_features(batch)
    assert table.values.shape==(6,17) and table.eligible.all()
    assert table.names[0]=='wind_max_ms'
    assert table.values[0,table.names.index('rain_total_mm')]==pytest.approx(batch.weather[:24,0,0,0].sum())
    assert table.keys[-1].horizon_id=='48_72'
    assert (Path(__file__).resolve().parents[2]/'outage_model/docs/FEATURES.md').read_text()==render_feature_docs(load_registry())


def test_later_issued_weather_rejected(upstream):
    store,m,key=upstream
    batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    batch.provenance['inputs']['fields'][0]['first_seen']='2099-01-01T00:00:00Z'
    with pytest.raises(ValueError,match='post-cutoff'):build_features(batch)


def test_zero_canopy_missing_neighbors_and_retrospective_static(upstream):
    store,m,key=upstream
    batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    batch.static.canopy[:]=0
    assert build_features(batch).eligible.all()
    batch.weather[0,1,0,0]=-9999
    assert not build_features(batch).eligible[:2].any()


def test_leap_year_season_phase(upstream):
    from datetime import datetime,timezone
    from dataclasses import replace
    store,m,key=upstream;batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    t=datetime(2024,2,29,tzinfo=timezone.utc)
    batch.manifest['cutoff']=t.isoformat();batch.manifest['target_start']=t.isoformat()
    for i,item in enumerate(batch.manifest['intervals']):
        item['start']=(t+timedelta(hours=i)).isoformat();item['end']=(t+timedelta(hours=i+1)).isoformat()
    for f in batch.provenance['inputs']['fields']:f['first_seen']=f['run']=t.isoformat()
    f=build_features(batch)
    phase=2*np.pi*(59+.5)/366
    assert f.values[0,f.names.index('season_sin')]==pytest.approx(np.sin(phase))
