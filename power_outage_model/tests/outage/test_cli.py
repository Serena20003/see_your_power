import json
import pytest
from outage_model.cli import main
from .test_archive import upstream
from outage_model.storage import BudgetStore,tree_bytes
from raster_pipeline.storage import LocalStore


def test_readiness_and_no_default_demo(tmp_path,capsys):
    assert main(['--store',str(tmp_path/'output'),'--budget-root',str(tmp_path),'readiness'])==0
    result=json.loads(capsys.readouterr().out)
    assert not result['ready_for_training'] and not result['ready_for_probability_release']


def test_budget_blocks_growth_without_deleting_existing_data(tmp_path):
    store=BudgetStore(LocalStore(tmp_path/'out'),tmp_path,limit=100)
    store.put('outage/v1/x',b'x'*30)
    with pytest.raises(ValueError,match='budget'):store.put('outage/v1/y',b'y'*80)
    assert store.read('outage/v1/x')[0]==b'x'*30
    with pytest.raises(ValueError):BudgetStore(store.store,tmp_path,limit=10_000_000_001)


def test_missing_training_inputs_fail_clearly(tmp_path,capsys):
    code=main(['--store',str(tmp_path/'out'),'--budget-root',str(tmp_path),'train',
        '--dataset-ref',str(tmp_path/'missing.json'),'--splits',str(tmp_path/'splits.json')])
    assert code==2
    assert 'error' in json.loads(capsys.readouterr().err)


def test_build_dataset_command_preserves_only_reviewed_cells(upstream,tmp_path,capsys):
    from .test_labels import event
    from outage_model.archive import archive_batch,load_archive
    from outage_model.features import build_features
    from outage_model.storage import put_bytes
    from raster_pipeline.contracts import canonical
    from outage_model.contracts import CoverageInterval
    from datetime import datetime,timedelta
    store,m,key=upstream;ref=archive_batch(store,key,test_mode=True);archive=load_archive(store,ref,test_mode=True)
    features=build_features(archive);start=datetime.fromisoformat(m['target_start']);freeze=start+timedelta(days=4)
    events=[event(canonical_id=f'event-{i}',original_event_id=str(i),onset=start+timedelta(hours=i*24+1),first_seen=freeze,last_seen=freeze).model_dump(mode='json') for i in range(3)]
    event_ref=put_bytes(store,'unit-test',canonical({'fixture':True,'source_snapshot':{'retrieved_at':freeze.isoformat()},'events':events}),'json')
    coverage=CoverageInterval(coverage_id='test',source='s',domain_id='whole-union',start=start,end=start+timedelta(days=3),footprint_version='test',
        cells=tuple((archive.grid.grid_id,0,col) for col in (0,1)),expected_polls=1,received_polls=1,complete=True,whole_cell=True,ascertainment_audited=True,evidence=('test-only',))
    coverage_ref=put_bytes(store,'unit-test',canonical([coverage.model_dump(mode='json')]),'json')
    metadata={k.id:{'state':'CA','region':'West','block_id':'storm','season':'Q4','spatial_boundary':False} for k in features.keys}
    meta_ref=put_bytes(store,'unit-test',canonical(metadata),'json')
    spec={'label_policy':{'version':'unit-test','reporting_lag_hours':0},'event_snapshot_refs':[event_ref],
          'coverage_ref':coverage_ref,'row_metadata_ref':meta_ref,'archives':[ref.model_dump(mode='json')]}
    path=tmp_path/'spec.json';path.write_bytes(canonical(spec))
    code=main(['--store',str(store.root),'--budget-root',str(store.root),'--test-mode','build-dataset',
        '--spec',str(path),'--freeze-time',freeze.isoformat()])
    captured=capsys.readouterr();assert code==0,captured.err
    from outage_model.splits import load_dataset
    d=load_dataset(store,json.loads(captured.out),test_mode=True)
    assert d.features.values.shape==(6,17) and d.labels.sum()==3
