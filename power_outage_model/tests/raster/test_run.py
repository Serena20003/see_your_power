from datetime import timedelta
import json
from pathlib import Path
import pytest
from raster_pipeline.run import run_fixture,recalculate,run
from raster_pipeline.risk import RiskConfig
from raster_pipeline.storage import LocalStore
from raster_pipeline.contracts import canonical
from test_windows import T


def test_end_to_end_repeat_and_changed_risk(tmp_path):
    s=LocalStore(tmp_path)
    m=run_fixture(T,s)
    assert run_fixture(T,s)['batch_id']==m['batch_id']
    assert m['fixture'] is True and m['static_version']
    assert len(m['intervals'])==72 and m['risk_config']['method']=='environmental_heuristic_v2'
    changed=run_fixture(T,s,risk_config=RiskConfig(canopy_weight=.2))
    assert changed['batch_id']!=m['batch_id']
    replay=recalculate(m,s,RiskConfig(canopy_weight=.3))
    assert replay['batch_id']!=changed['batch_id']
    assert replay['intervals'][0]['weather']==m['intervals'][0]['weather']


def test_failed_attempt_preserves_last_good(tmp_path):
    s=LocalStore(tmp_path);m=run_fixture(T,s)
    old=s.read('raster/v1/latest.json')[0]
    with pytest.raises(ValueError):run_fixture(T+timedelta(hours=1),s,missing_hour=-1)
    assert s.read('raster/v1/latest.json')[0]==old
    attempts=[json.loads((tmp_path/p).read_text()) for p in s.keys('raster/v1/attempts')]
    assert any(x['status']=='incomplete' and x['failure_reason'] for x in attempts)


def test_dry_run_does_not_publish(tmp_path):
    s=LocalStore(tmp_path)
    result=run_fixture(T,s,dry_run=True)
    assert result['status']=='dry_run'
    assert s.read('raster/v1/latest.json')[0] is None


def test_retry_after_crash_before_manifest_uses_same_immutable_provenance(tmp_path,monkeypatch):
    import importlib
    module=importlib.import_module('raster_pipeline.run')
    store=LocalStore(tmp_path);publish=module.publish_batch
    def crash(*args,**kwargs):raise RuntimeError('simulated crash before manifest')
    monkeypatch.setattr(module,'publish_batch',crash)
    with pytest.raises(RuntimeError,match='simulated'):run_fixture(T,store)
    monkeypatch.setattr(module,'publish_batch',publish)
    m=run_fixture(T,store)
    assert len(m['intervals'])==72
    assert json.loads(store.read('raster/v1/latest.json')[0])['batch_id']==m['batch_id']


def test_cutoff_changes_with_same_target_and_inputs_do_not_duplicate_batches(tmp_path):
    # The cutoff is provenance; two eligible polls within the same target hour
    # must reuse the source/config/static/target identity.
    from raster_pipeline.run import fixture_static
    from raster_pipeline.fixtures import generate_fixture,load_fixture_config
    from raster_pipeline.contracts import GridSpec
    grid=GridSpec(grid_id='fixture-two-cells-v1',west=-120,south=34,east=-118,north=35,resolution=1)
    root=tmp_path/'fixture';generate_fixture(root,T,grid)
    store=LocalStore(tmp_path/'store');static=fixture_static(grid,store)
    cfg=load_fixture_config(root).model_copy(update={'static_manifest':f'raster/v1/static/{static.static_version}/manifest.json'})
    cfg=cfg.model_copy(update={'catalog':[f.model_copy(update={'first_seen':T-timedelta(hours=1)}) for f in cfg.catalog]})
    a=run(cfg,T-timedelta(minutes=59),store,fixture_dir=root,work_dir=tmp_path/'work')
    b=run(cfg,T-timedelta(minutes=29),store,fixture_dir=root,work_dir=tmp_path/'work')
    assert a['batch_id']==b['batch_id'] and a['cutoff']==b['cutoff']


def test_batch_identity_changes_with_pinned_geometry_and_recipe(monkeypatch):
    from raster_pipeline.run import batch_identity
    from raster_pipeline.contracts import SourceSelection
    from raster_pipeline.config import PipelineConfig
    from raster_pipeline.risk import RiskConfig
    from raster_pipeline.decode import tool_versions
    s=SourceSelection(cutoff=T,target_start=T,selected_run=T,intervals=[],fields=[])
    base=PipelineConfig(native_grid_sha256='a'*64)
    args=(s,[],base,RiskConfig(),{'key':'static','sha256':'s'},False)
    first,_=batch_identity(*args)
    changed,_=batch_identity(s,[],base.model_copy(update={'native_grid_sha256':'b'*64}),*args[3:])
    assert first!=changed
    monkeypatch.setattr('raster_pipeline.decode.tool_versions',lambda:{'cdo':'new version'})
    assert batch_identity(*args)[0]!=first


def test_recalculate_preserves_archived_weather_recipe(tmp_path,monkeypatch):
    store=LocalStore(tmp_path);m=run_fixture(T,store)
    monkeypatch.setattr('raster_pipeline.decode.tool_versions',lambda:{'cdo':'a-new-runtime-version'})
    replay=recalculate(m,store,RiskConfig(canopy_weight=.25))
    assert replay['weather_recipe']==m['weather_recipe']


def test_completed_batch_keeps_processed_replay_without_archiving_raw_grib(tmp_path):
    store=LocalStore(tmp_path);m=run_fixture(T,store)
    assert store.keys('raster/v1/raw')==[]
    inventory=json.loads(store.read(m['inputs']['key'])[0])
    assert all(f['sha256'] and f['url'] and 'raw_key' not in f for f in inventory['fields'])
    replay=recalculate(m,store,RiskConfig(canopy_weight=.27))
    assert replay['intervals'][0]['weather']==m['intervals'][0]['weather']
