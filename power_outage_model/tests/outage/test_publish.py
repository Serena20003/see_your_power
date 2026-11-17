from datetime import datetime,timedelta
import pytest
from outage_model.archive import archive_batch,load_archive
from outage_model.predict import predict_batch
from outage_model.publish import publish_batch,rollback,LATEST_TEST
from .test_archive import upstream
from .test_predict import compatible_bundle


def test_publication_corruption_and_race_preserve_pointer(upstream):
    store,m,key=upstream;batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    pred=predict_batch(batch,compatible_bundle(batch),{'store':store,'test_mode':True,'now':datetime.fromisoformat(m['cutoff'])})
    assert publish_batch(pred,store,None,test_mode=True).status=='published'
    old,generation=store.read(LATEST_TEST)
    assert publish_batch(pred,store,None,test_mode=True).status=='already_published'
    obj=pred.windows[-1]['probability'];store.path(obj['key']).write_bytes(b'bad')
    with pytest.raises(ValueError,match='checksum'):publish_batch(pred,store,generation,test_mode=True)
    assert store.read(LATEST_TEST)[0]==old


def test_missing_window_rejected(upstream):
    store,m,key=upstream;batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    pred=predict_batch(batch,compatible_bundle(batch),{'store':store,'test_mode':True,'now':datetime.fromisoformat(m['cutoff'])})
    broken=pred.model_copy(update={'windows':pred.windows[:2]})
    with pytest.raises(ValueError,match='three windows'):publish_batch(broken,store,None,test_mode=True)
    assert store.read(LATEST_TEST)[0] is None


def test_pointer_race_and_explicit_rollback(upstream):
    from outage_model.publish import rollback
    store,m,key=upstream;batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    b=compatible_bundle(batch);now=datetime.fromisoformat(m['cutoff'])
    first=predict_batch(batch,b,{'store':store,'test_mode':True,'now':now})
    assert publish_batch(first,store,None,test_mode=True).status=='published'
    _,gen=store.read(LATEST_TEST)
    stale=predict_batch(batch,b,{'store':store,'test_mode':True,'now':now+timedelta(hours=3)})
    assert publish_batch(stale,store,'wrong-generation',test_mode=True).status=='superseded'
    assert publish_batch(stale,store,gen,test_mode=True).status=='published'
    _,stale_gen=store.read(LATEST_TEST)
    assert publish_batch(first,store,stale_gen,test_mode=True).status=='superseded'
    _,gen=store.read(LATEST_TEST)
    assert rollback(store,gen,test_mode=True)
    import json
    assert json.loads(store.read(LATEST_TEST)[0])['batch_id']==first.batch_id
