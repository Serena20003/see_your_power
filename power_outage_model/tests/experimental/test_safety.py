from datetime import date
import io,json
import numpy as np
import pytest
from shapely.geometry import Polygon
from raster_pipeline.storage import LocalStore
from raster_pipeline.contracts import canonical
from outage_model.storage import put_bytes
from outage_model.experimental.contracts import SamplingPolicy,PREFIX
from outage_model.experimental.sampling import sample_parent
from outage_model.experimental.dataset import load_dataset,make_parents
from .test_train import fixture_dataset


def test_degenerate_geometry_stops_instead_of_infinite_sampling():
    parent={'parent_id':'p','county_fips':'01001','anchor_date':'2018-01-01','horizon':0,'count':1,'role':'train'}
    with pytest.raises(ValueError,match='geometry'):sample_parent(parent,Polygon(),SamplingPolicy())


def test_corrupt_parent_assignment_and_nonfinite_targets_rejected(tmp_path):
    store=LocalStore(tmp_path);ref=fixture_dataset(store)
    meta=json.loads(store.read(ref['key'])[0]);raw=store.read(meta['matrix']['key'])[0]
    with np.load(io.BytesIO(raw),allow_pickle=False) as archive:arrays={k:archive[k] for k in archive.files}
    arrays['role'][0]=2
    out=io.BytesIO();np.savez_compressed(out,**arrays)
    meta['matrix']=put_bytes(store,PREFIX,out.getvalue(),'npz');ref=put_bytes(store,PREFIX,canonical(meta),'json')
    with pytest.raises(ValueError,match='parent assignment'):load_dataset(store,ref,test_mode=True)
    arrays['role'][0]=0;arrays['y'][0]=np.nan
    out=io.BytesIO();np.savez_compressed(out,**arrays);meta['matrix']=put_bytes(store,PREFIX,out.getvalue(),'npz');ref=put_bytes(store,PREFIX,canonical(meta),'json')
    with pytest.raises(ValueError,match='target'):load_dataset(store,ref,test_mode=True)


def test_truncated_real_transfer_never_promotes_reference(tmp_path):
    import hashlib
    from outage_model.experimental.history import prepare_history
    body=b'fips_code,customers_out,run_start_time\n01001,3,2018-01-01 00:00:00\n'
    class Response:
        status_code=206
        def __init__(self,metadata=False):
            self.metadata=metadata;self.headers={'content-range':f'bytes 0-{len(body)-1}/{len(body)}'}
        def raise_for_status(self):pass
        def json(self):return {'files':[{'name':'eaglei_outages_2018.csv','size':len(body),'download_url':'https://test.invalid/data','computed_md5':hashlib.md5(body).hexdigest()}]}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def iter_bytes(self,chunk_size):yield body[:-4]
    class Client:
        def get(self,url):return Response(True)
        def stream(self,*args,**kwargs):return Response()
    store=LocalStore(tmp_path/'out')
    with pytest.raises(ValueError,match='truncated'):prepare_history(store,root=tmp_path,client=Client())
    assert store.read(f'{PREFIX}/history/2018-reference.json')[0] is None


def test_cli_network_failure_is_json_without_fixture_fallback(monkeypatch,capsys):
    import httpx
    from outage_model.experimental.cli import main
    import outage_model.experimental.history as history
    def fail(*args,**kwargs):raise httpx.ReadTimeout('source unavailable')
    monkeypatch.setattr(history,'prepare_history',fail)
    assert main(['prepare'])==2
    message=json.loads(capsys.readouterr().err)
    assert message['error']=='ReadTimeout' and message['fixture_fallback'] is False
