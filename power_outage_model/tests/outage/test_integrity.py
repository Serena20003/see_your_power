from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone,timedelta
import json
import time as clock
import pytest
from raster_pipeline.contracts import canonical,digest
from raster_pipeline.storage import LocalStore
from outage_model.storage import BudgetStore,tree_bytes
from outage_model.evaluate import evaluate,load_release_config
from outage_model.contracts import BaselineBundle
from outage_model.splits import make_splits
from .test_explain import model_bundle
from .helpers import dataset
from .test_archive import upstream


def test_evaluation_rejects_changed_split():
    d,b=model_bundle();_,p=dataset(40);s=make_splits(d,p)
    assignments=dict(s.assignments);assignments[d.features.keys[0].id]='test'
    edited=s.model_copy(update={'assignments':assignments})
    with pytest.raises(ValueError,match='split'):
        evaluate(b,BaselineBundle.model_validate(b.baselines),d,edited,{'release_policy':load_release_config(),'scopes':[]})


def test_budget_check_and_write_are_atomic(tmp_path):
    class SlowStore(LocalStore):
        def put(self,key,data):clock.sleep(.03);return super().put(key,data)
    store=BudgetStore(SlowStore(tmp_path/'out'),tmp_path,limit=100)
    def write(i):
        try:store.put(f'outage/v1/{i}',b'x'*30);return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(write,range(4)))
    assert tree_bytes(tmp_path)<=100
    assert not all(results)


def test_archive_validates_extra_raw_dependency_on_reload(upstream):
    from outage_model.archive import archive_batch,load_archive
    store,m,key=upstream
    data=b'test-only-raw-asset';store.put('assets/raw.bin',data)
    import hashlib
    ref=archive_batch(store,key,test_mode=True,normalization_assets=[{'key':'assets/raw.bin','sha256':hashlib.sha256(data).hexdigest(),'role':'test'}])
    record=json.loads(store.read(ref.manifest_key)[0]);store.path(record['normalization_assets'][0]['key']).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):load_archive(store,ref,test_mode=True)


def test_prediction_cutoff_is_bound_to_archive(upstream):
    from outage_model.archive import archive_batch,load_archive
    from outage_model.predict import predict_batch
    from outage_model.publish import validate_batch
    from .test_predict import compatible_bundle
    store,m,key=upstream;archive=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    b=predict_batch(archive,compatible_bundle(archive),{'store':store,'test_mode':True,'now':datetime.fromisoformat(m['cutoff'])})
    altered=b.model_copy(update={'cutoff':b.cutoff+timedelta(minutes=1)})
    with pytest.raises(ValueError,match='lineage|timestamp'):validate_batch(altered,store,test_mode=True)


def test_arbitrary_json_is_not_an_audit():
    from outage_model.evaluate import validate_audit_reports
    d,b=model_bundle();_,policy=dataset(40);s=make_splits(d,policy)
    with pytest.raises(ValueError,match='audit'):validate_audit_reports({'lineage_audit':{'passed':True}},b,d,s)


def test_final_period_consumption_is_atomic_and_cannot_be_reused(tmp_path):
    from outage_model.evaluate import consume_final_period
    store=LocalStore(tmp_path)
    claim={'dataset_id':'d','candidate_hash':'c','split_hash':'s','gate_decision_hash':'g',
        'test_start':'2020-01-01T00:00:00Z','test_end':'2020-06-01T00:00:00Z'}
    def consume(_):
        try:consume_final_period(store,claim,test_mode=True);return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=2) as pool:assert sum(pool.map(consume,range(2)))==1
    with pytest.raises(ValueError,match='future|consumed'):consume_final_period(store,{**claim,'dataset_id':'different-dataset'},test_mode=True)
    consume_final_period(store,{**claim,'dataset_id':'new','test_start':'2020-07-01T00:00:00Z','test_end':'2020-12-01T00:00:00Z'},test_mode=True)
