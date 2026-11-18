from datetime import datetime,timezone
import numpy as np
from outage_model.support import fit_support, assess_support
from outage_model.splits import make_splits, role_indices, subset_features
from .helpers import dataset


def test_training_only_support_and_scope_statuses():
    d,p=dataset(30);s=make_splits(d,p);idx=role_indices(d,s,('train',))
    f=subset_features(d.features,idx)
    support=fit_support(f,[d.rows[i]['block_id'] for i in idx],{})
    scopes=[{'scope_id':'s','grid_id':'fixture','horizon_id':'0_24','season':d.rows[0]['season'],'cells':[[0,0]],'region':'West'}]
    result=assess_support(subset_features(f,[0]),support,scopes,datetime.now(timezone.utc))
    assert result['status'][0]==1
    assert assess_support(subset_features(f,[0]),support,[],datetime.now(timezone.utc))['status'][0]==2
    bad=subset_features(f,[0]);values=bad.values.copy();values[0,0]=1e8
    bad=bad.model_copy(update={'values':values})
    assert assess_support(bad,support,scopes,datetime.now(timezone.utc))['status'][0]==3
