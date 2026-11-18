import numpy as np
import pytest
from outage_model.explain import explain
from outage_model.artifacts import assemble_bundle, save_bundle, load_bundle
from outage_model.contracts import FeatureRow, CalibrationBundle
from outage_model.train import fit_trees
from outage_model.baselines import fit_baselines
from outage_model.support import fit_support
from outage_model.splits import make_splits, role_indices, subset_features
from raster_pipeline.storage import LocalStore
from .helpers import dataset
from .test_train import small_config


def model_bundle():
    d,p=dataset(40);s=make_splits(d,p)
    trees=fit_trees(d,s,small_config(),test_mode=True)
    baselines=fit_baselines(d,s,{},test_mode=True)
    idx=role_indices(d,s,('train',))
    support=fit_support(subset_features(d.features,idx),[d.rows[i]['block_id'] for i in idx],{})
    cal={h:CalibrationBundle(slope=1.2,intercept=-.3,offsets={'West':.1,'Northwest':-.1}) for h in trees.models}
    return d,assemble_bundle(trees,baselines,cal,support,d)


def test_full_contributions_and_native_reload(tmp_path):
    d,bundle=model_bundle()
    row=FeatureRow(key=d.features.keys[0],names=d.features.names,values=tuple(d.features.values[0]),
        region='West',provenance=d.features.provenance,status='test_only')
    result=explain(bundle,row)
    assert result['base_raw_margin']+sum(result['raw_contributions'].values())==pytest.approx(result['raw_margin'],abs=1e-6)
    assert result['calibrated_base']+sum(result['calibrated_contributions'].values())+result['calibration_intercept']+result['regional_offset']==pytest.approx(result['calibrated_log_odds'],abs=1e-6)
    assert sum(result['family_contributions'].values())==pytest.approx(sum(result['calibrated_contributions'].values()),abs=1e-6)
    store=LocalStore(tmp_path);ref=save_bundle(bundle,store);restored=load_bundle(ref,store,test_mode=True)
    assert explain(restored,row)==result
    store.path(f'outage/v1/models/{ref.model_id}/0_24.txt').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):load_bundle(ref,store,test_mode=True)
