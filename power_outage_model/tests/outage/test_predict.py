from datetime import datetime,timezone,timedelta
from dataclasses import replace
import numpy as np
import pytest
from outage_model.predict import predict_batch
from outage_model.publish import validate_batch
from outage_model.archive import archive_batch,load_archive
from .test_archive import upstream
from .test_explain import model_bundle
from outage_model.artifacts import assemble_bundle,save_bundle
from outage_model.evaluate import candidate_hash


def compatible_bundle(batch):
    d,b=model_bundle()
    # Test-only trained models have the same registry; align test static identity.
    b=b.model_copy(update={'grid_id':batch.grid.grid_id,'static_version':batch.manifest['static_version'],
        'canopy_vintage':str(batch.manifest['canopy_vintage'])})
    return b.model_copy(update={'model_id':candidate_hash(b)})


def test_unvalidated_cells_have_only_separate_weather_score(upstream):
    store,m,key=upstream;batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    b=compatible_bundle(batch)
    now=datetime.fromisoformat(m['cutoff'])
    pred=predict_batch(batch,b,{'store':store,'test_mode':True,'now':now})
    assert len(pred.windows)==3
    data=validate_batch(pred,store,test_mode=True)
    for h,arrays in data.items():
        assert (arrays['status']==2).all()
        assert (arrays['probability']==-9999).all()
        offset={'0_24':0,'24_48':24,'48_72':48}[h]
        np.testing.assert_allclose(arrays['fallback'],batch.fallback[offset:offset+24].max(0))
    assert pred.cutoff==now


def test_old_weather_does_not_reset_freshness(upstream):
    store,m,key=upstream;batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    b=compatible_bundle(batch);now=datetime.fromisoformat(m['cutoff'])+timedelta(hours=3)
    pred=predict_batch(batch,b,{'store':store,'test_mode':True,'now':now})
    arrays=validate_batch(pred,store,test_mode=True)
    assert all((a['status']==5).all() and (a['fallback']==-9999).all() for a in arrays.values())


def test_supported_test_fixture_gets_reconstructible_probability(upstream):
    from outage_model.contracts import ReleaseEvidence
    from outage_model.features import build_features
    from outage_model.support import fit_support
    from outage_model.evaluate import candidate_hash,load_release_config
    from raster_pipeline.contracts import digest
    store,m,key=upstream;batch=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    b=compatible_bundle(batch);features=build_features(batch)
    support=fit_support(features,[str(k.col) for k in features.keys],{})
    b=b.model_copy(update={'support':support});b=b.model_copy(update={'model_id':candidate_hash(b)})
    scopes=[{'scope_id':h,'grid_id':batch.grid.grid_id,'horizon_id':h,'season':'Q4','cells':[[0,0],[0,1]],'region':'West','passed':True} for h in ('0_24','24_48','48_72')]
    evidence=ReleaseEvidence(dataset_id=b.training['dataset_id'],policy_hash=digest(load_release_config()),candidate_hash=candidate_hash(b),
        split_hash=b.training['split_hash'],role='test',scopes=tuple(scopes),metrics={},passed=True,fixture=True)
    b=b.model_copy(update={'evidence':evidence})
    pred=predict_batch(batch,b,{'store':store,'test_mode':True,'now':datetime.fromisoformat(m['cutoff'])})
    arrays=validate_batch(pred,store,test_mode=True)
    assert all((a['status']==1).all() for a in arrays.values())
    assert all(np.all((a['probability']>=0)&(a['probability']<=1)) for a in arrays.values())
