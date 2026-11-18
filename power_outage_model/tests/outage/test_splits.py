from datetime import timedelta
import numpy as np
import pytest
from outage_model.contracts import DataReadinessError
from outage_model.splits import make_splits, role_indices, save_dataset, load_dataset
from raster_pipeline.storage import LocalStore
from .helpers import dataset


def test_five_roles_and_parquet_roundtrip(tmp_path):
    d,p=dataset(30); s=make_splits(d,p)
    assert {s.assignments[k.id] for k in d.features.keys}=={'train','tune','calibrate','gate','test'}
    ref=save_dataset(d,LocalStore(tmp_path))
    restored=load_dataset(LocalStore(tmp_path),ref,test_mode=True)
    assert restored.features.names==d.features.names
    np.testing.assert_array_equal(restored.features.values,d.features.values)
    assert restored.dataset_id==d.dataset_id


def test_shared_storm_and_incident_purged_across_roles():
    d,p=dataset(30); rows=[dict(x) for x in d.rows]
    rows[0]['block_id']=rows[90]['block_id']='cross-state-storm'
    rows[1]['event_ids']=rows[91]['event_ids']=('shared-onset',)
    d=d.model_copy(update={'rows':tuple(rows)})
    s=make_splits(d,p)
    assert d.features.keys[0].id not in s.assignments
    assert d.features.keys[90].id not in s.assignments
    assert d.features.keys[1].id not in s.assignments


def test_holdouts_never_fit_or_calibrate():
    d,p=dataset(30);p=p.model_copy(update={'held_out_states':('CA',)})
    s=make_splits(d,p)
    for i in role_indices(d,s,('train','tune','calibrate')):
        assert d.rows[i]['state']!='CA'


def test_inadequate_history_fails():
    d,p=dataset(1)
    with pytest.raises(DataReadinessError):make_splits(d,p)
