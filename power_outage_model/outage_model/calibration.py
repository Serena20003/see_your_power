"""Nonnegative sigmoid calibration and penalized, sum-zero regional offsets."""
from collections import Counter
from datetime import timedelta
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from raster_pipeline.contracts import digest
from .contracts import CalibrationBundle, DataReadinessError, HORIZONS, time
from .splits import role_indices


def fit_sigmoid(margins,labels,regions,blocks,*,penalty=None,min_positive=100,min_negative=1000,min_blocks=20):
    f=np.asarray(margins,dtype=float);y=np.asarray(labels,dtype=float);r=np.asarray(regions);blocks=np.asarray(blocks)
    if len(f)<2 or len(np.unique(y))!=2 or not np.isfinite(f).all() or np.ptp(f)<1e-10:
        raise DataReadinessError('degenerate calibration: finite variable margins and both classes required')
    counts={}
    for region in sorted(set(r)):
        idx=r==region;counts[str(region)]={'positive':int(y[idx].sum()),'negative':int((1-y[idx]).sum()),'blocks':len(set(blocks[idx]))}
    eligible=[region for region,c in counts.items() if c['positive']>=min_positive and c['negative']>=min_negative and c['blocks']>=min_blocks] if penalty is not None else []
    # A single eligible region has a zero offset under the identifying constraint.
    if len(eligible)<2:eligible=[]
    Z=np.column_stack([r==region for region in eligible]).astype(float) if eligible else np.zeros((len(f),0))
    def objective(theta):
        raw=theta[2:];delta=raw-raw.mean() if len(raw) else raw
        z=theta[0]*f+theta[1]+Z@delta;res=expit(z)-y
        loss=np.sum(np.logaddexp(0,z)-y*z)+(penalty or 0)*float(delta@delta)/2
        grad_delta=Z.T@res+(penalty or 0)*delta
        grad_delta=grad_delta-grad_delta.mean() if len(grad_delta) else grad_delta
        return float(loss),np.r_[res@f,res.sum(),grad_delta]
    initial=np.r_[1.,np.log((y.sum()+1)/(len(y)-y.sum()+1)),np.zeros(len(eligible))]
    fitted=minimize(objective,initial,jac=True,method='L-BFGS-B',bounds=[(0,None),(None,None)]+[(None,None)]*len(eligible),
        options={'maxiter':2000,'ftol':1e-12,'gtol':1e-7})
    if not fitted.success or not np.isfinite(fitted.x).all():raise DataReadinessError('calibration optimization failed')
    delta=fitted.x[2:];delta=delta-delta.mean() if len(delta) else delta
    return CalibrationBundle(slope=float(fitted.x[0]),intercept=float(fitted.x[1]),
        offsets={region:float(v) for region,v in zip(eligible,delta)},penalty=penalty,counts=counts,
        selection={'method':'penalized MAP; zero-centered Gaussian offset prior; sum(delta)=0','rows':len(y),
                   'margins_labels_hash':digest({'margins':f.tolist(),'labels':y.tolist(),'regions':r.tolist(),'blocks':blocks.tolist()})})


def calibrate(bundle,raw_margin,region_id):
    offset=bundle.offsets.get(region_id,0.)
    z=float(bundle.slope*raw_margin+bundle.intercept+offset)
    return {'probability':float(expit(z)),'calibrated_log_odds':z,'regional_offset':offset,
            'regional_fallback':region_id not in bundle.offsets,'slope':bundle.slope,'intercept':bundle.intercept}


def calibrated_array(bundle,f,regions):
    f=np.asarray(f);return expit(bundle.slope*f+bundle.intercept+np.asarray([bundle.offsets.get(r,0) for r in regions]))


def fit_calibration(raw_margins,labels,region_ids,splits,config):
    dataset=config['dataset'];f=np.asarray(raw_margins);y=np.asarray(labels);regions=np.asarray(region_ids)
    if dataset.features.fixture!=config.get('test_mode',False):raise ValueError('fixture/live calibration mixing forbidden')
    result={}
    for h in HORIZONS:
        idx=role_indices(dataset,splits,('calibrate',),h)
        blocks=np.asarray([r['block_id'] for r in dataset.rows])
        global_fit=fit_sigmoid(f[idx],y[idx],regions[idx],blocks[idx])
        ordered=sorted(set(blocks[idx]),key=lambda b:min(time(dataset.rows[i]['cutoff']) for i in idx if blocks[i]==b))
        trials=[]
        for penalty in (1.,10.,100.):
            losses=[]
            for fraction in (.5,.75):
                split_at=max(1,int(len(ordered)*fraction))
                later=set(ordered[split_at:]);early=set(ordered[:split_at])
                valid=np.asarray([i for i in idx if blocks[i] in later],int)
                if not len(valid):continue
                start=min(time(dataset.rows[i]['cutoff']) for i in valid)
                train=np.asarray([i for i in idx if blocks[i] in early and time(dataset.rows[i]['end'])+timedelta(hours=72)<=start],int)
                try:
                    fit=fit_sigmoid(f[train],y[train],regions[train],blocks[train],penalty=penalty)
                    losses.append(float(np.mean((calibrated_array(fit,f[valid],regions[valid])-y[valid])**2)))
                except DataReadinessError:continue
            trials.append({'penalty':penalty,'inner_brier':float(np.mean(losses)) if losses else None,'eligible_folds':len(losses)})
        available=[t for t in trials if t['inner_brier'] is not None]
        penalty=min(available,key=lambda x:(x['inner_brier'],x['penalty']))['penalty'] if available else 100.
        regional=fit_sigmoid(f[idx],y[idx],regions[idx],blocks[idx],penalty=penalty)
        regional=regional.model_copy(update={'selection':{**regional.selection,'inner_chronological_trials':trials,
            'penalty_fallback':not bool(available)}})
        result[h]={'global':global_fit,'regional':regional}
    return result


def choose_calibration(candidates,margins,labels,regions,dataset,splits):
    """Compare on gate-validation only; test cannot influence the choice."""
    result={};regions=np.asarray(regions);y=np.asarray(labels);f=np.asarray(margins)
    for h,choices in candidates.items():
        idx=role_indices(dataset,splits,('gate',),h)
        a=choices['global'];b=choices['regional']
        pa=calibrated_array(a,f[idx],regions[idx]);pb=calibrated_array(b,f[idx],regions[idx])
        macro_a=[];macro_b=[];nonworsening=True
        for region in sorted(set(regions[idx])):
            mask=regions[idx]==region
            aa=float(np.mean((pa[mask]-y[idx][mask])**2));bb=float(np.mean((pb[mask]-y[idx][mask])**2))
            macro_a.append(aa);macro_b.append(bb);nonworsening&=bb<=aa+1e-12
        use=bool(b.offsets) and bool(len(idx)) and nonworsening and np.mean(macro_b)<np.mean(macro_a) and np.mean((pb-y[idx])**2)<=np.mean((pa-y[idx])**2)
        selected=b if use else a
        result[h]=selected.model_copy(update={'selection':{**selected.selection,'selected_on':'gate',
            'regional_selected':bool(use),'gate_row_ids_hash':digest([dataset.features.keys[i].id for i in idx])}})
    return result
