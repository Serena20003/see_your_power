"""Train-only Beta-smoothed seasonal frequency and standardized logistic baselines."""
from collections import Counter
import numpy as np
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import log_loss
from .contracts import BaselineBundle, HORIZONS, DataReadinessError
from .splits import role_indices


def check_fit(dataset,splits,test_mode):
    if dataset.features.fixture!=test_mode:raise DataReadinessError('fixture/live training mixing forbidden')
    if splits.dataset_id!=dataset.dataset_id:raise ValueError('split identity mismatch')
    if not dataset.lineage.get('verified_labels'):raise DataReadinessError('verified label evidence required')
    if not test_mode and not dataset.features.provenance.get('normalization_reproducible'):
        raise DataReadinessError('complete raw normalization archive required')
    if len(dataset.labels)!=len(dataset.features.keys) or not np.isin(dataset.labels,[0,1]).all():
        raise DataReadinessError('missing/nonbinary labels')
    if not dataset.features.eligible.all():raise DataReadinessError('ineligible input rows')
    for h in HORIZONS:
        for role in ('train','tune'):
            i=role_indices(dataset,splits,(role,),h)
            if len(i)<2 or len(np.unique(dataset.labels[i]))<2:raise DataReadinessError(f'two-class {role}/{h} rows required')


def fit_baselines(dataset,splits,config,*,test_mode=False):
    check_fit(dataset,splits,test_mode)
    models={};X=dataset.features.values;y=dataset.labels
    for h in HORIZONS:
        train=role_indices(dataset,splits,('train',),h);tune=role_indices(dataset,splits,('tune',),h)
        scaler=StandardScaler().fit(X[train]);xtrain=scaler.transform(X[train]);xtune=scaler.transform(X[tune])
        candidates=[]
        for c in config.get('logistic_C',[.01,.1,1,10]):
            model=LogisticRegression(C=c,max_iter=2000,random_state=42,solver='lbfgs').fit(xtrain,y[train])
            loss=float(log_loss(y[tune],model.predict_proba(xtune)[:,1],labels=[0,1]))
            candidates.append((loss,c,model))
        loss,c,model=min(candidates,key=lambda x:(x[0],x[1]))
        pooled={'positive':int(y[train].sum()),'total':len(train)}
        pooled['probability']=(pooled['positive']+1)/(pooled['total']+2)
        seasonal={}
        for season in ('Q1','Q2','Q3','Q4'):
            idx=[i for i in train if dataset.rows[i]['season']==season]
            n=len(idx);positive=int(y[idx].sum())
            seasonal[season]={'positive':positive,'total':n,'fallback':n<config.get('seasonal_min_rows',100),
                'probability':(positive+1)/(n+2) if n>=config.get('seasonal_min_rows',100) else pooled['probability']}
        models[h]={'pooled':pooled,'seasonal':seasonal,'logistic':{'C':c,'mean':scaler.mean_.tolist(),
            'scale':scaler.scale_.tolist(),'coefficients':model.coef_[0].tolist(),'intercept':float(model.intercept_[0]),
            'tune_log_loss':loss,'trials':[{'C':v,'tune_log_loss':l} for l,v,_ in candidates]}}
    return BaselineBundle(models=models,feature_names=dataset.features.names,
        training={'dataset_id':dataset.dataset_id,'split_hash':__import__('raster_pipeline.contracts',fromlist=['digest']).digest(splits),
                  'fixture':test_mode,'prior':'Beta(1,1)','season':'UTC calendar quarter'})


def baseline_predictions(bundle,features,rows):
    if tuple(features.names)!=tuple(bundle.feature_names):raise ValueError('baseline feature order mismatch')
    seasonal=np.zeros(len(rows));logistic=np.zeros(len(rows))
    for h in HORIZONS:
        idx=np.asarray([i for i,k in enumerate(features.keys) if k.horizon_id==h],dtype=int)
        if not len(idx):continue
        m=bundle.models[h];b=m['logistic']
        logistic[idx]=expit(((features.values[idx]-np.asarray(b['mean']))/np.asarray(b['scale']))@np.asarray(b['coefficients'])+b['intercept'])
        seasonal[idx]=[m['seasonal'].get(rows[i]['season'],m['pooled'])['probability'] for i in idx]
    return {'seasonal':seasonal,'logistic':logistic}
