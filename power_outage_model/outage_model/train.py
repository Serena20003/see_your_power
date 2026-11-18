"""Deterministic bounded LightGBM search, with no fitting during inference."""
from collections import Counter
import importlib.metadata
import itertools
import json
from pathlib import Path
import platform
import subprocess
import numpy as np
import lightgbm as lgb
from sklearn.metrics import log_loss
from raster_pipeline.contracts import digest
from .contracts import HORIZONS, TreeBundle, DataReadinessError
from .baselines import check_fit
from .features import load_registry
from .splits import role_indices


def load_train_config():return json.loads((Path(__file__).parent/'configs/train-v1.json').read_text())


def training_weights(domains,scheme):
    if scheme=='natural':return np.ones(len(domains))
    if scheme!='balanced':raise ValueError('unknown weighting scheme')
    counts=Counter(domains);median=float(np.median(list(counts.values())))
    raw=np.asarray([np.clip(median/counts[g],.2,5) for g in domains])
    return raw/raw.mean()


def environment():
    try:revision=subprocess.check_output(['git','rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
    except (OSError,subprocess.CalledProcessError):revision='unavailable'
    return {'python':platform.python_version(),'platform':platform.platform(),'code_revision':revision,
        'packages':{p:importlib.metadata.version(p) for p in ('numpy','scipy','lightgbm','scikit-learn','pyarrow','pydantic')},
        'source_sha256':digest({p.name:p.read_text() for p in sorted(Path(__file__).parent.glob('*.py'))})}


def _params(config,params):
    return {'objective':'binary','metric':'binary_logloss','learning_rate':.03,'verbosity':-1,
        'bagging_fraction':1.,'bagging_freq':0,'feature_fraction':1.,'seed':42,'data_random_seed':42,
        'feature_fraction_seed':42,'bagging_seed':42,'deterministic':True,'force_col_wise':True,
        'num_threads':config.get('threads',4),**params}


def _fit(X,y,train,tune,names,params,weights,config):
    if len(train)<2 or len(tune)<2 or len(np.unique(y[train]))!=2 or len(np.unique(y[tune]))!=2:
        raise DataReadinessError('fold lacks independent two-class history')
    a=lgb.Dataset(X[train],label=y[train],weight=weights,feature_name=list(names),free_raw_data=True)
    b=lgb.Dataset(X[tune],label=y[tune],reference=a,feature_name=list(names),free_raw_data=True)
    return lgb.train(_params(config,params),a,num_boost_round=config['rounds'],valid_sets=[b],
        callbacks=[lgb.early_stopping(config['early_stopping'],verbose=False)])


def _macro_loss(y,p,groups):
    return float(np.mean([log_loss(y[np.asarray(groups)==g],p[np.asarray(groups)==g],labels=[0,1]) for g in sorted(set(groups))]))


def _geographic_loss(dataset,X,train,tune,names,params,scheme,config):
    losses=[];audits=[];y=dataset.labels
    for kind in ('state','domain_id'):
        for group in sorted({dataset.rows[i][kind] for i in tune}):
            a=np.asarray([i for i in train if dataset.rows[i][kind]!=group],dtype=int)
            b=np.asarray([i for i in tune if dataset.rows[i][kind]==group],dtype=int)
            try:
                weights=training_weights([dataset.rows[i]['domain_id'] for i in a],scheme)
                booster=_fit(X,y,a,b,names,params,weights,config)
                loss=float(log_loss(y[b],booster.predict(X[b],num_threads=config.get('threads',4)),labels=[0,1]))
                losses.append(loss);audits.append({'kind':kind,'group':group,'log_loss':loss,'eligible':True})
            except DataReadinessError as exc:audits.append({'kind':kind,'group':group,'eligible':False,'reason':str(exc)})
    return (float(np.mean(losses)) if losses else None),audits


def fit_trees(dataset,splits,config=None,*,test_mode=False):
    config=config or load_train_config();check_fit(dataset,splits,test_mode)
    expected=tuple(f['name'] for f in load_registry()['features'])
    if dataset.features.names!=expected:raise ValueError('only registered predictor order admitted')
    if not test_mode:
        # Test simplifications may not become an accidental production shortcut.
        if config!=load_train_config():raise ValueError('live training requires pinned train-v1 policy')
    grid=[dict(config['initial'])]
    if config.get('challengers',True):
        for depth,leaves,minimum,l2 in itertools.product((3,5),(7,15),(100,500),(1,10)):
            p=dict(max_depth=depth,num_leaves=leaves,min_data_in_leaf=minimum,lambda_l2=l2)
            if leaves<=2**depth and p not in grid:grid.append(p)
    X=dataset.features.values;y=dataset.labels;trials=[];models={};records={}
    for h in HORIZONS:
        train=role_indices(dataset,splits,('train',),h);tune=role_indices(dataset,splits,('tune',),h)
        best=None
        for params,scheme in itertools.product(grid,config.get('weight_schemes',['natural','balanced'])):
            weights=training_weights([dataset.rows[i]['domain_id'] for i in train],scheme)
            booster=_fit(X,y,train,tune,expected,params,weights,config)
            pred=booster.predict(X[tune],num_threads=config.get('threads',4))
            loss=float(log_loss(y[tune],pred,labels=[0,1]))
            macro=_macro_loss(y[tune],pred,[dataset.rows[i]['domain_id'] for i in tune])
            geo,audit=_geographic_loss(dataset,X,train,tune,expected,params,scheme,config) if config.get('geographic_folds',True) else (None,[])
            score=geo if geo is not None else macro
            record={'horizon':h,'parameters':params,'weighting':scheme,'row_log_loss':loss,'macro_domain_log_loss':macro,
                'geographic_log_loss':geo,'geographic_folds':audit,'selection_loss':score,'best_iteration':booster.best_iteration,
                'feature_names':list(expected),'kind':'candidate'}
            trials.append(record)
            if best is None or score<best[0]:best=(score,booster,record,weights)
        _,booster,record,weights=best
        # Family ablations are development-only, using frozen best hyperparameters.
        selected=list(expected);families={f['family'] for f in load_registry()['features']}
        if config.get('ablations',True):
            for family in sorted(families):
                names=[f['name'] for f in load_registry()['features'] if f['name'] in selected and f['family']!=family]
                if not names:continue
                indices=[expected.index(n) for n in names];XX=X[:,indices]
                challenger=_fit(XX,y,train,tune,names,record['parameters'],weights,config)
                p=challenger.predict(XX[tune],num_threads=config.get('threads',4))
                loss=_macro_loss(y[tune],p,[dataset.rows[i]['domain_id'] for i in tune])
                geo,audit=_geographic_loss(dataset,XX,train,tune,names,record['parameters'],record['weighting'],config) if config.get('geographic_folds',True) else (None,[])
                score=geo if geo is not None else loss
                trials.append({'horizon':h,'kind':'family_ablation','removed_family':family,'selection_loss':score,
                    'feature_names':names,'geographic_folds':audit,'best_iteration':challenger.best_iteration})
                if score<best[0]:
                    best=(score,challenger,record,weights);booster=challenger;selected=names
        text=booster.model_to_string(num_iteration=booster.best_iteration)
        native=lgb.Booster(model_str=text)
        models[h]=text
        records[h]={**record,'selected_features':native.feature_name(),'best_iteration':native.current_iteration(),
            'tree_count':native.num_trees(),'tree_dump':native.dump_model(),'selection_loss':best[0],
            'weight_counts':dict(Counter(dataset.rows[i]['domain_id'] for i in train)),
            'weight_sha256':digest(weights.tolist()),'training_row_keys_sha256':digest([dataset.features.keys[i].id for i in train]),
            'weight_formula':'clip(median(n_g)/n_g,0.2,5), then mean-one; training domains only'}
    return TreeBundle(models=models,feature_names=expected,records=records,trials=tuple(trials),
        training={'dataset_id':dataset.dataset_id,'split_hash':digest(splits),'configuration':config,'environment':environment(),
            'fixture':test_mode,'geographic_selection_ineligible':any(t.get('geographic_log_loss') is None for t in trials if t['kind']=='candidate')})


def raw_margins(bundle,features):
    if tuple(bundle.feature_names)!=tuple(features.names):raise ValueError('tree feature schema/order mismatch')
    models=bundle.models if hasattr(bundle,'models') else bundle.trees
    result=np.full(len(features.keys),np.nan)
    for h in HORIZONS:
        idx=np.asarray([i for i,k in enumerate(features.keys) if k.horizon_id==h and features.eligible[i]],dtype=int)
        if not len(idx):continue
        booster=lgb.Booster(model_str=models[h]);columns=[features.names.index(n) for n in booster.feature_name()]
        result[idx]=booster.predict(features.values[np.ix_(idx,columns)],raw_score=True,num_iteration=booster.current_iteration(),num_threads=4)
    return result
