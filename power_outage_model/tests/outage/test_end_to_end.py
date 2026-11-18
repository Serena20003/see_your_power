"""Generated inputs in this test are exclusively software-mechanics evidence."""
from datetime import datetime
from outage_model.train import fit_trees,raw_margins
from outage_model.baselines import fit_baselines
from outage_model.calibration import fit_calibration,choose_calibration
from outage_model.support import fit_support
from outage_model.splits import make_splits,role_indices,subset_features
from outage_model.artifacts import assemble_bundle,save_bundle,load_bundle
from outage_model.archive import archive_batch,load_archive
from outage_model.predict import predict_batch
from outage_model.publish import publish_batch,validate_batch
from outage_model.evaluate import candidate_hash
from .helpers import dataset
from .test_train import small_config
from .test_archive import upstream


def test_offline_training_to_three_window_outputs(upstream):
    d,p=dataset(60);splits=make_splits(d,p)
    trees=fit_trees(d,splits,small_config(),test_mode=True)
    baseline=fit_baselines(d,splits,{},test_mode=True)
    margins=raw_margins(trees,d.features);regions=[r['region'] for r in d.rows]
    calibration=fit_calibration(margins,d.labels,regions,splits,{'dataset':d,'test_mode':True})
    chosen=choose_calibration(calibration,margins,d.labels,regions,d,splits)
    idx=role_indices(d,splits,('train',));support=fit_support(subset_features(d.features,idx),[d.rows[i]['block_id'] for i in idx],{})
    bundle=assemble_bundle(trees,baseline,chosen,support,d)
    store,m,key=upstream;archive=load_archive(store,archive_batch(store,key,test_mode=True),test_mode=True)
    bundle=bundle.model_copy(update={'grid_id':archive.grid.grid_id,'static_version':m['static_version'],'canopy_vintage':str(m['canopy_vintage'])})
    bundle=bundle.model_copy(update={'model_id':candidate_hash(bundle)})
    ref=save_bundle(bundle,store);restored=load_bundle(ref,store,test_mode=True)
    pred=predict_batch(archive,restored,{'store':store,'test_mode':True,'now':datetime.fromisoformat(m['cutoff'])})
    assert len(pred.explanations)==3
    assert set(validate_batch(pred,store,test_mode=True))=={'0_24','24_48','48_72'}
    assert publish_batch(pred,store,None,test_mode=True).status=='published'
