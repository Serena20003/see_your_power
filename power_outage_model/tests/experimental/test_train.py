import io
import numpy as np
import pytest
from raster_pipeline.storage import LocalStore
from raster_pipeline.contracts import canonical
from outage_model.storage import put_bytes
from outage_model.experimental.contracts import FEATURES,SEMANTICS,PREFIX
from outage_model.experimental.train import train_model,load_model
from outage_model.experimental.predict import predict_points


def fixture_dataset(store):
    rng=np.random.default_rng(42);X=rng.normal(size=(320,8)).astype('float32');X[:,0]-=100;X[:,1]+=35
    y=np.log1p(np.maximum(0,4*(X[:,1]-34))).astype('float32');pidx=np.repeat(np.arange(80),4)
    role=np.repeat(np.repeat(np.arange(4),20),4).astype('uint8')
    parents=[{'parent_id':str(i),'county_fips':f'{i%4+1:05d}','anchor_date':'2018-01-01','target_date':'2018-01-01',
        'horizon':i%3,'count':float(np.expm1(y[i*4:i*4+4]).sum()),'role':('train','tune','test','county_holdout')[i//20]} for i in range(80)]
    buffer=io.BytesIO();np.savez_compressed(buffer,X=X,y=y,weights=np.full(len(y),.25,dtype='float32'),role=role,parent_index=pidx)
    meta={**SEMANTICS,'schema_version':'experimental-random-point-dataset/v1','fixture':True,'features':list(FEATURES),'rows':len(X),
        'matrix':put_bytes(store,PREFIX,buffer.getvalue(),'npz'),'parents':put_bytes(store,PREFIX,canonical(parents),'json'),
        'sampling_policy':{'seed':42,'points_per_parent':4},'history_ref':{},'geometry_ref':{},'county_fips':[p['county_fips'] for p in parents]}
    return put_bytes(store,PREFIX,canonical(meta),'json')


def test_fixture_cannot_be_silently_used_for_real_fitting(tmp_path):
    store=LocalStore(tmp_path);ref=fixture_dataset(store)
    with pytest.raises(ValueError,match='fixture'):train_model(store,ref)


def test_native_reload_and_explicit_simulation_metadata(tmp_path):
    store=LocalStore(tmp_path);ref=fixture_dataset(store)
    trained=train_model(store,ref,test_mode=True,rounds=12)
    model,meta=load_model(store,trained['model_ref'],test_mode=True)
    X=np.zeros((2,len(FEATURES)),dtype='float32');X[:,0]=-100;X[:,1]=35
    pred=predict_points(store,trained['model_ref'],X,FEATURES,test_mode=True)
    assert pred['experimental'] and pred['locations_simulated']
    assert pred['physical_outage_probability'] is False
    assert np.isfinite(pred['allocated_customer_impact']).all()
    np.testing.assert_allclose(np.maximum(0,np.expm1(model.predict(X,num_threads=1))),pred['allocated_customer_impact'])
    with pytest.raises(ValueError,match='order'):predict_points(store,trained['model_ref'],X,FEATURES[::-1],test_mode=True)


def test_portable_zip_predicts_without_training_objects(tmp_path):
    import json,zipfile
    from shapely.geometry import box,mapping
    from outage_model.experimental.portable import export_bundle
    from outage_model.storage import checked_read
    store=LocalStore(tmp_path/'source');ref=fixture_dataset(store);trained=train_model(store,ref,test_mode=True,rounds=12)
    meta=json.loads(checked_read(store,trained['model_ref']))
    geometry={'type':'FeatureCollection','features':[{'type':'Feature','properties':{'GEOID':'00001'},'geometry':mapping(box(-101,34,-99,36))}],
        'metadata':{'fixture':True}}
    meta['geometry_ref']=put_bytes(store,PREFIX,canonical(geometry),'geojson')
    model_ref=put_bytes(store,PREFIX,canonical(meta),'json')
    exported=export_bundle(store,model_ref,test_mode=True)
    fresh=LocalStore(tmp_path/'fresh')
    with zipfile.ZipFile(io.BytesIO(checked_read(store,exported['bundle_ref']))) as bundle:
        assert 'ASSUMPTIONS.md' in bundle.namelist()
        assert 'provenance/source-history-manifest.json' in bundle.namelist()
        for name in bundle.namelist():
            if name.startswith('output/'):fresh.put(name[len('output/'):],bundle.read(name))
        copied_ref=json.loads(bundle.read('model-ref.json'))
    X=np.zeros((2,len(FEATURES)),dtype='float32');X[:,0]=-100;X[:,1]=35
    a=predict_points(store,model_ref,X,FEATURES,test_mode=True)
    b=predict_points(fresh,copied_ref,X,FEATURES,test_mode=True)
    assert a==b
    assert fresh.read(meta['dataset_ref']['key'])[0] is None
