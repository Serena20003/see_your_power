"""Event-block uncertainty and fail-closed, explicit fixed-cell release evidence."""
from collections import Counter
import json
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit
from sklearn.metrics import average_precision_score, log_loss
from raster_pipeline.contracts import digest
from .contracts import ReleaseEvidence, HORIZONS, DataReadinessError
from .splits import role_indices


def load_release_config():return json.loads((Path(__file__).parent/'configs/release-v1.json').read_text())


def candidate_hash(bundle):return digest(bundle.model_dump(mode='json',exclude={'model_id','evidence'}))


def _calibration_line(y,p):
    if len(np.unique(y))<2 or np.ptp(p)<1e-12:return {'intercept':None,'slope':None}
    f=logit(np.clip(p,1e-12,1-1e-12))
    def loss(t):
        z=t[0]+t[1]*f;res=expit(z)-y
        return np.sum(np.logaddexp(0,z)-y*z),np.array([res.sum(),res@f])
    fit=minimize(loss,[0.,1.],jac=True,method='BFGS')
    return {'intercept':float(fit.x[0]),'slope':float(fit.x[1])} if np.isfinite(fit.x).all() else {'intercept':None,'slope':None}


def metrics(labels,probabilities,*,bins=10,thresholds=(.01,.05,.1,.2,.5)):
    y=np.asarray(labels,dtype=int);p=np.asarray(probabilities,dtype=float)
    if not len(y) or not np.isfinite(p).all() or np.any((p<0)|(p>1)):raise ValueError('finite probabilities and nonempty rows required')
    reliability=[]
    for idx in np.array_split(np.argsort(p,kind='stable'),bins):
        if not len(idx):reliability.append({'count':0,'mean_probability':None,'frequency':None,'gap':None});continue
        n=len(idx);freq=float(y[idx].mean());mean=float(p[idx].mean());z=1.959963984540054
        center=(freq+z*z/(2*n))/(1+z*z/n);radius=z*np.sqrt(freq*(1-freq)/n+z*z/(4*n*n))/(1+z*z/n)
        reliability.append({'count':n,'mean_probability':mean,'frequency':freq,'gap':abs(mean-freq),
            'probability_interval':[float(p[idx].min()),float(p[idx].max())],
            'frequency_wilson_95':[float(center-radius),float(center+radius)]})
    alerts=[]
    for threshold in thresholds:
        pred=p>=threshold;tp=int(np.sum(pred&(y==1)));fp=int(np.sum(pred&(y==0)));fn=int(np.sum(~pred&(y==1)))
        alerts.append({'threshold':threshold,'recall':tp/(tp+fn) if tp+fn else None,
            'precision':tp/(tp+fp) if tp+fp else None,'false_alerts':fp,'true_alerts':tp})
    return {'rows':len(y),'positive':int(y.sum()),'negative':int((1-y).sum()),'prevalence':float(y.mean()),
        'brier':float(np.mean((p-y)**2)),'log_loss':float(log_loss(y,p,labels=[0,1])),
        'pr_auc':float(average_precision_score(y,p)) if len(np.unique(y))==2 else None,
        'calibration':_calibration_line(y,p),'reliability':reliability,'alerts':alerts}


def _weighted_brier(y,p,domains,counts=None):
    error=(p-y)**2
    if counts is None:return float(np.mean(error))
    # Equal-domain objective recomputed inside each resampled block replicate.
    weights=np.asarray([1/counts[g] for g in domains]);return float(np.average(error,weights=weights))


def block_skill_interval(labels,probabilities,baseline,blocks,domains,*,replicates=1000,seed=42,macro=False):
    y=np.asarray(labels);p=np.asarray(probabilities);b=np.asarray(baseline);blocks=np.asarray(blocks);domains=np.asarray(domains)
    unique=sorted(set(blocks))
    if len(unique)<2:return None
    indices={block:np.where(blocks==block)[0] for block in unique};rng=np.random.default_rng(seed);skills=[]
    for _ in range(replicates):
        idx=np.concatenate([indices[block] for block in rng.choice(unique,len(unique),replace=True)])
        if len(np.unique(y[idx]))<2:return None
        counts=Counter(domains[idx]) if macro else None
        base=_weighted_brier(y[idx],b[idx],domains[idx],counts)
        if base<=0:return None
        skills.append(1-_weighted_brier(y[idx],p[idx],domains[idx],counts)/base)
    return tuple(float(v) for v in np.quantile(skills,[.025,.975]))


def evaluate_scope(y,p,baselines,rows,config):
    y=np.asarray(y);p=np.asarray(p);failures=[]
    if not len(y):return {'passed':False,'failures':['no_rows'],'metrics':{}}
    m=metrics(y,p,bins=config['reliability_bins'],thresholds=config['thresholds'])
    blocks=[r['block_id'] for r in rows];domains=[r['domain_id'] for r in rows]
    days={r['start'][:10] for r in rows}
    if int(y.sum())<config['minimum_positive'] or int((1-y).sum())<config['minimum_negative'] or len(set(blocks))<config['minimum_blocks'] or len(days)<config['minimum_days']:
        failures.append('minimum_evidence')
    m.update(independent_blocks=len(set(blocks)),distinct_days=len(days),baseline_comparison={})
    for name,baseline in baselines.items():
        b=np.asarray(baseline);score=float(np.mean((b-y)**2));skill=1-m['brier']/score if score>0 else None
        intervals={}
        for macro in (False,True):
            ci=block_skill_interval(y,p,b,blocks,domains,replicates=config['bootstrap_replicates'],seed=config['bootstrap_seed'],macro=macro) if 'minimum_evidence' not in failures else None
            intervals['macro_domain' if macro else 'pooled']=ci
            if ci is None or ci[0]<=0:failures.append(f'{name}_skill_lower_bound_'+('macro' if macro else 'pooled'))
        m['baseline_comparison'][name]={'brier':score,'brier_skill':skill,'block_bootstrap_95':intervals}
    bins=m['reliability']
    if any(b['count']<config['minimum_bin_rows'] for b in bins):failures.append('insufficient_reliability_bins')
    else:
        gaps=[b['gap'] for b in bins]
        if np.mean(gaps)>config['mean_absolute_calibration_gap'] or max(gaps)>config['maximum_calibration_gap']:failures.append('reliability_gap')
    # Each eligible geography/hazard slice must not conceal negative skill.
    slices={};seasonal=np.asarray(baselines['seasonal'])
    for kind in ('state','domain_id','block_id','region','season','wind_severity','rain_severity','temperature_severity','canopy_slice','spatial_boundary'):
        values=sorted({str(r.get(kind,'unreported')) for r in rows})
        for value in values:
            idx=np.asarray([i for i,r in enumerate(rows) if str(r.get(kind,'unreported'))==value],int)
            yy=y[idx];pp=p[idx];bb=seasonal[idx]
            eligible=bool(yy.sum()>=config['minimum_positive'] and (1-yy).sum()>=config['minimum_negative'] and len({blocks[i] for i in idx})>=config['minimum_blocks'] and len({rows[i]['start'][:10] for i in idx})>=config['minimum_days'])
            base=float(np.mean((bb-yy)**2));skill=1-float(np.mean((pp-yy)**2))/base if base>0 else None
            slices[f'{kind}:{value}']={'eligible':eligible,'rows':len(idx),'brier_skill':skill,'unsupported':not eligible}
            if eligible and (skill is None or skill<0):failures.append(f'negative_slice_skill:{kind}:{value}')
    for name,slice_result in slices.items():
        kind,value=name.split(':',1)
        if kind!='block_id' and (value=='unreported' or not slice_result['eligible']):
            failures.append('unsupported_scope_slice:'+name)
    m['slices']=slices
    m['macro_brier']={kind:float(np.mean([np.mean((p[np.asarray([str(r.get(kind))==v for r in rows])]-y[np.asarray([str(r.get(kind))==v for r in rows])])**2) for v in sorted({str(r.get(kind)) for r in rows})])) for kind in ('state','domain_id','block_id')}
    return {'passed':not failures,'failures':sorted(set(failures)),'metrics':m}


def evaluate(candidate,baselines,dataset,splits,config,*,role='gate'):
    from .train import raw_margins
    from .calibration import calibrated_array
    from .baselines import baseline_predictions
    if role not in ('gate','test'):raise ValueError('evaluation role must be gate or untouched test')
    if candidate.training['dataset_id']!=dataset.dataset_id:raise ValueError('evaluation dataset differs from training snapshot')
    if candidate.training['split_hash']!=digest(splits):raise ValueError('evaluation split differs from frozen training split')
    from .splits import validate_splits
    validate_splits(dataset,splits)
    policy=config['release_policy'];scopes=config.get('scopes',[])
    if not dataset.features.fixture:
        canonical_policy=load_release_config()
        if policy!=canonical_policy:raise ValueError('release policy must match pinned policy before final test')
    if role=='test':
        frozen=config.get('gate_evidence')
        if frozen is None or frozen.role!='gate' or frozen.dataset_id!=dataset.dataset_id or frozen.split_hash!=digest(splits) or frozen.candidate_hash!=candidate_hash(candidate) or frozen.policy_hash!=digest(policy):
            raise ValueError('frozen gate decision required before final test')
        scopes=[s for s in frozen.scopes if s.get('passed')]
    validate_audit_reports(config.get('audit_reports',{}),candidate,dataset,splits)
    idx=role_indices(dataset,splits,(role,));f=raw_margins(candidate,dataset.features)
    regions=[r['region'] for r in dataset.rows];prob=np.full(len(dataset.labels),np.nan)
    for h in HORIZONS:
        hi=[i for i in idx if dataset.features.keys[i].horizon_id==h]
        prob[hi]=calibrated_array(candidate.calibrations[h],f[hi],[regions[i] for i in hi])
    baseline=baseline_predictions(baselines,dataset.features,dataset.rows)
    reports=[];failures=[]
    for scope in scopes:
        cells={tuple(c) for c in scope.get('cells',[])}
        ii=[i for i in idx if dataset.features.keys[i].grid_id==scope['grid_id'] and dataset.features.keys[i].horizon_id==scope['horizon_id']
            and dataset.rows[i]['season']==scope['season'] and (dataset.features.keys[i].row,dataset.features.keys[i].col) in cells
            and dataset.rows[i]['domain_id']==scope['domain_id'] and dataset.rows[i]['region']==scope['region']]
        result=evaluate_scope(dataset.labels[ii],prob[ii],{k:v[ii] for k,v in baseline.items()},evaluation_rows(candidate,dataset,ii),policy)
        observed={(dataset.features.keys[i].row,dataset.features.keys[i].col) for i in ii}
        if cells-observed or not scope.get('coverage_evidence'):
            result['passed']=False;result['failures'].append('unevaluated_cells_or_unverified_footprint')
        # Every real release also needs transfer/ablation/explanation/lineage audits;
        # their hashed artifacts are mandatory, not inferred from fit success.
        required=('lineage_audit','explanation_audit','geographic_holdouts','storm_holdouts','grouped_importance')
        if any(not config.get('audit_refs',{}).get(k) for k in required):
            result['passed']=False;result['failures'].append('missing_required_audit_artifacts')
        reports.append({**scope,**result})
        if not result['passed']:failures.append(scope['scope_id'])
    if not reports:failures.append('no_frozen_validation_scopes')
    return ReleaseEvidence(dataset_id=dataset.dataset_id,policy_hash=digest(policy),candidate_hash=candidate_hash(candidate),
        split_hash=digest(splits),role=role,scopes=tuple(reports),metrics={'evaluated_rows':len(idx),'audit_refs':config.get('audit_refs',{})},
        passed=bool(reports) and not failures,fixture=dataset.features.fixture,failures=tuple(failures))


def evaluation_rows(candidate,dataset,indices):
    """Severity bins frozen from the training-only support reference."""
    result=[]
    names=dataset.features.names
    for i in indices:
        row=dict(dataset.rows[i]);h=dataset.features.keys[i].horizon_id
        support=candidate.support['horizons'][h]
        reference=np.asarray(support['reference'])*np.asarray(support['scale'])+np.asarray(support['median'])
        for target,feature,quantiles in (
            ('wind_severity','wind_max_ms',(.9,.99)),('rain_severity','rain_total_mm',(.9,.99)),
            ('temperature_severity','temp_mean_c',(.1,.9)),('canopy_slice','canopy_fraction',(.333333,.666667))):
            j=names.index(feature);edges=np.quantile(reference[:,j],quantiles)
            row[target]=str(int(np.searchsorted(edges,dataset.features.values[i,j],side='right')))
        result.append(row)
    return result


def validate_audit_reports(reports,candidate,dataset,splits):
    """Existence of a JSON object is not evidence of a successful audit."""
    required=('lineage_audit','explanation_audit','geographic_holdouts','storm_holdouts','grouped_importance')
    for name in required:
        report=reports.get(name)
        if not isinstance(report,dict) or report.get('schema_version')!='outage-audit/v1':raise ValueError('missing/invalid audit schema: '+name)
        if report.get('candidate_hash')!=candidate_hash(candidate) or report.get('dataset_id')!=dataset.dataset_id or report.get('split_hash')!=digest(splits):
            raise ValueError('audit identity mismatch: '+name)
        if report.get('kind')!=name or report.get('uses_final_test') is not False or report.get('passed') is not True:
            raise ValueError('unsuccessful or test-contaminated audit: '+name)
        if name in ('geographic_holdouts','storm_holdouts') and report.get('eligible_folds',0)<2:
            raise ValueError('insufficient independent eligible holdout folds: '+name)
        if not report.get('results'):raise ValueError('empty audit results: '+name)
    return True


def consume_final_period(store,claim,*,test_mode=False):
    """Atomically consume a final period; another dataset cannot reuse its dates."""
    from datetime import timedelta
    from .contracts import time
    from raster_pipeline.contracts import canonical
    prefix='outage/v1/'+('testing/' if test_mode else '')+'evaluations'
    head_key=prefix+'/final-test-head.json';raw,generation=store.read(head_key)
    start=time(claim['test_start']);end=time(claim['test_end'])
    if end<=start:raise ValueError('invalid final-test period')
    if raw:
        previous=json.loads(raw)
        if start<time(previous['test_end'])+timedelta(hours=72):
            raise ValueError('final-test dates already consumed; a new future period is required')
    if not store.replace_pointer(head_key,canonical(claim),generation):
        raise ValueError('final-test period consumed by concurrent job')
    key=f"{prefix}/{claim['dataset_id']}/final-test-consumed.json"
    if not store.replace_pointer(key,canonical(claim),None):
        raise ValueError('final-test dataset already consumed')
    return True
