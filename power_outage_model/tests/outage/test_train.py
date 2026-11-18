import numpy as np
import pytest
import lightgbm as lgb
from outage_model.train import fit_trees, raw_margins, training_weights, load_train_config
from outage_model.baselines import fit_baselines, baseline_predictions
from outage_model.splits import make_splits, role_indices
from outage_model.contracts import DataReadinessError
from .helpers import dataset


def small_config():
    c=load_train_config();c.update(rounds=25,early_stopping=5,challengers=False,ablations=False,geographic_folds=False)
    c['initial']={**c['initial'],'min_data_in_leaf':5}
    return c


def test_live_training_rejects_fixture():
    d,p=dataset(30)
    with pytest.raises(DataReadinessError,match='fixture'):fit_trees(d,make_splits(d,p),small_config())


def test_three_native_models_and_reconstruction():
    d,p=dataset(40);s=make_splits(d,p)
    trees=fit_trees(d,s,small_config(),test_mode=True)
    margins=raw_margins(trees,d.features)
    assert margins.shape==(len(d.labels),) and set(trees.models)=={'0_24','24_48','48_72'}
    for h,text in trees.models.items():
        model=lgb.Booster(model_str=text)
        idx=[i for i,k in enumerate(d.features.keys) if k.horizon_id==h]
        np.testing.assert_allclose(model.predict(d.features.values[idx],raw_score=True,num_threads=1),margins[idx],atol=1e-12)
        assert model.num_trees()==trees.records[h]['tree_count']
    again=fit_trees(d,s,small_config(),test_mode=True)
    np.testing.assert_allclose(raw_margins(again,d.features),margins,atol=1e-12)


def test_weights_and_baseline_scaler_use_train_only():
    w=training_weights(['a']*20+['b']*2,'balanced')
    assert w[-1]>w[0] and w.mean()==pytest.approx(1)
    d,p=dataset(40);s=make_splits(d,p)
    b=fit_baselines(d,s,{},test_mode=True)
    idx=role_indices(d,s,('train',),'0_24')
    np.testing.assert_allclose(b.models['0_24']['logistic']['mean'],d.features.values[idx].mean(0),rtol=1e-5,atol=1e-6)
    pred=baseline_predictions(b,d.features,d.rows)
    assert set(pred)=={'seasonal','logistic'}
    assert all(np.all((x>0)&(x<1)) for x in pred.values())
