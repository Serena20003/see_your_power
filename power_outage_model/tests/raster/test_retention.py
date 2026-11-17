from datetime import datetime,timezone,timedelta
import json
from raster_pipeline.retention import collect_garbage
from raster_pipeline.storage import LocalStore
from raster_pipeline.run import run_fixture
from raster_pipeline.contracts import canonical
from test_windows import T


def test_retention_preserves_old_latest_and_static_but_deletes_unreferenced(tmp_path):
    s=LocalStore(tmp_path);m=run_fixture(T,s)
    # Even beyond both retention windows, current last-good dependencies survive.
    old=m['batch_id'];cutoff=datetime.fromisoformat(m['published_at'])+timedelta(days=40)
    abandoned={**m,'batch_id':'abandoned','published_at':(cutoff-timedelta(days=31)).isoformat()}
    s.put('raster/v1/runs/abandoned/manifest.json',canonical(abandoned))
    s.put('raster/v1/runs/abandoned/risk/orphan.tif',b'orphan')
    removed=collect_garbage(s,now=cutoff,dry_run=True)
    assert 'raster/v1/runs/abandoned/risk/orphan.tif' in removed
    assert not any(f'/runs/{old}/' in k for k in removed)
    assert not any('/static/' in k for k in removed)
    collect_garbage(s,now=cutoff,dry_run=False)
    assert s.read(m['intervals'][0]['weather']['key'])[0] is not None


def test_raw_inputs_have_no_archive_window_even_for_retained_batches(tmp_path):
    s=LocalStore(tmp_path);m=run_fixture(T,s)
    raw_key='raster/v1/raw/legacy-source.grib2';s.put(raw_key,b'legacy source')
    inputs_key='raster/v1/runs/legacy/inputs.json'
    s.put(inputs_key,canonical({'fields':[{'raw_key':raw_key}]}))
    legacy={**m,'batch_id':'legacy','inputs':{'key':inputs_key}}
    s.put('raster/v1/runs/legacy/manifest.json',canonical(legacy))
    removed=collect_garbage(s,dry_run=True)
    assert raw_key in removed
    assert m['intervals'][0]['weather']['key'] not in removed
    assert inputs_key in removed
    assert s.read(raw_key)[0] is not None
    collect_garbage(s,dry_run=False)
    assert s.read(raw_key)[0] is None
    assert s.read(m['intervals'][0]['weather']['key'])[0] is not None


def test_no_30_day_tail_for_superseded_manifests(tmp_path):
    s=LocalStore(tmp_path);m=run_fixture(T,s)
    s.put('raster/v1/runs/abandoned/manifest.json',canonical({**m,'batch_id':'abandoned'}))
    s.put('raster/v1/attempts/old.json',b'{}')
    s.put('raster/v1/source-catalog.json',b'[]')
    now=datetime.fromisoformat(m['published_at'])+timedelta(days=8)
    removed=collect_garbage(s,now=now)
    assert 'raster/v1/runs/abandoned/manifest.json' in removed
    assert 'raster/v1/attempts/old.json' in removed
    assert 'raster/v1/source-catalog.json' not in removed


def test_only_latest_and_previous_are_kept_with_shared_replay_dependencies(tmp_path):
    from raster_pipeline.run import recalculate
    from raster_pipeline.risk import RiskConfig
    from raster_pipeline.publish import publish_batch,LATEST
    s=LocalStore(tmp_path);a=run_fixture(T,s)
    b=recalculate(a,s,RiskConfig(canopy_weight=.2))
    before_c,generation=s.read(LATEST)
    c=recalculate(b,s,RiskConfig(canopy_weight=.3))
    pointer=json.loads(s.read(LATEST)[0])
    assert pointer['previous']['batch_id']==b['batch_id']
    manifests={k for k in s.keys('raster/v1/runs') if k.endswith('/manifest.json')}
    assert manifests=={f"raster/v1/runs/{m['batch_id']}/manifest.json" for m in (b,c)}
    assert s.read(a['intervals'][0]['risk']['key'])[0] is None
    # Both retained risk batches reuse A's weather, despite A being evicted.
    assert s.read(a['intervals'][0]['weather']['key'])[0] is not None
    assert s.read(b['static_manifest']['key'])[0] is not None
    assert s.read(b['supporting_intervals'][0]['weather']['key'])[0] is not None
    assert s.read(c['supporting_intervals'][0]['weather']['key'])[0] is not None
    # Neither time nor an identical retry rotates out the recovery batch.
    expired=collect_garbage(s,now=datetime.now(timezone.utc)+timedelta(days=100))
    assert not any('/runs/' in k or '/static/' in k for k in expired)
    original=s.read(LATEST)[0]
    assert publish_batch(c,s,s.read(LATEST)[1]).status=='already_published'
    assert s.read(LATEST)[0]==original
    assert publish_batch(b,s,generation).status=='superseded'
    assert s.read(LATEST)[0]==original
    # Once both retained batches use newer weather, shared files from the
    # evicted original must disappear too, without waiting seven days.
    pending=f"raster/v1/runs/{a['batch_id']}/new-attempt.bin"
    s.put(pending,b'new staging created after retirement')
    d=run_fixture(T+timedelta(hours=1),s)
    e=run_fixture(T+timedelta(hours=2),s)
    assert s.read(a['intervals'][0]['weather']['key'])[0] is None
    assert s.read(d['intervals'][0]['weather']['key'])[0] is not None
    assert s.read(e['intervals'][0]['weather']['key'])[0] is not None
    assert s.read(pending)[0] is not None


def test_unpublished_or_failed_batches_cannot_take_the_recovery_slot(tmp_path):
    import pytest
    from raster_pipeline.run import recalculate
    from raster_pipeline.risk import RiskConfig
    from raster_pipeline.publish import publish_batch,LATEST
    s=LocalStore(tmp_path);a=run_fixture(T,s)
    b=recalculate(a,s,RiskConfig(canopy_weight=.2))
    before,generation=s.read(LATEST)
    broken={**b,'batch_id':'broken','intervals':b['intervals'][:-1]}
    with pytest.raises(ValueError):publish_batch(broken,s,generation)
    assert s.read(LATEST)[0]==before
    # A completed manifest whose pointer was never published is not a backup.
    candidate={**b,'batch_id':'unpublished','published_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
    key='raster/v1/runs/unpublished/manifest.json';s.put(key,canonical(candidate))
    removed=collect_garbage(s)
    assert key in removed and s.read(key)[0] is not None  # dry-run only
    assert a['intervals'][0]['risk']['key'] not in removed
    assert b['intervals'][0]['risk']['key'] not in removed
    collect_garbage(s,dry_run=False)
    assert json.loads(s.read(LATEST)[0])['previous']['batch_id']==a['batch_id']
    loser={**b,'batch_id':'lost-pointer-race'}
    assert publish_batch(loser,s,'outdated-generation').status=='superseded'
    assert s.read(LATEST)[0]==before
    assert s.read('raster/v1/runs/lost-pointer-race/manifest.json')[0] is None


def test_legacy_pointer_finds_a_valid_older_recovery_batch(tmp_path):
    from raster_pipeline.run import recalculate
    from raster_pipeline.risk import RiskConfig
    from raster_pipeline.publish import LATEST
    s=LocalStore(tmp_path);a=run_fixture(T,s)
    b=recalculate(a,s,RiskConfig(canopy_weight=.2))
    raw,generation=s.read(LATEST);legacy=json.loads(raw)
    legacy.pop('previous',None);legacy['schema_version']='raster-latest/v1'
    assert s.replace_pointer(LATEST,canonical(legacy),generation)
    removed=collect_garbage(s)
    assert a['intervals'][0]['risk']['key'] not in removed
    assert b['intervals'][0]['risk']['key'] not in removed


def test_cleanup_error_does_not_relabel_a_valid_publication(tmp_path,monkeypatch,caplog):
    def fail_cleanup(*args,**kwargs):raise OSError('simulated storage deletion failure')
    monkeypatch.setattr('raster_pipeline.retention.collect_garbage',fail_cleanup)
    s=LocalStore(tmp_path);m=run_fixture(T,s)
    assert json.loads(s.read('raster/v1/latest.json')[0])['batch_id']==m['batch_id']
    assert m['quality_status']=='complete'
    assert 'cleanup_failed' in caplog.text


def test_interrupted_cleanup_resumes_without_restarting_a_seven_day_window(tmp_path,monkeypatch):
    import pytest
    s=LocalStore(tmp_path);m=run_fixture(T,s)
    manifest_key='raster/v1/runs/retired/manifest.json'
    leftover='raster/v1/runs/retired/weather/leftover.tif'
    s.put(manifest_key,canonical({**m,'batch_id':'retired'}));s.put(leftover,b'obsolete')
    original=s.delete
    def interrupted(key):
        original(key)
        if key==manifest_key:raise OSError('crash after deleting manifest')
    monkeypatch.setattr(s,'delete',interrupted)
    with pytest.raises(OSError):collect_garbage(s,dry_run=False)
    monkeypatch.setattr(s,'delete',original)
    assert leftover in collect_garbage(s)
    collect_garbage(s,dry_run=False)
    assert s.read(leftover)[0] is None
    assert s.keys('raster/v1/retired')==[]
    assert s.read(m['intervals'][0]['weather']['key'])[0] is not None
