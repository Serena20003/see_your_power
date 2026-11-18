"""Development-only transfer experiments, grouped diagnostics and region routing."""
from datetime import timedelta
from pathlib import Path
import json
import numpy as np
from shapely.geometry import shape,Point,box
from shapely.strtree import STRtree
from raster_pipeline.contracts import digest
from .features import load_registry
from .contracts import DataReadinessError,HORIZONS,FeatureRow
from .splits import role_indices,subset_features


def route_regions(grid,state_geojson,*,expected_sha256):
    if digest(state_geojson)!=expected_sha256:raise ValueError('state footprint checksum mismatch')
    config=json.loads((Path(__file__).parent/'configs/regions-v1.json').read_text())
    region_by_state={state:r for r,states in config['regions'].items() for state in states}
    features=state_geojson['features'];geometries=[shape(f['geometry']) for f in features];tree=STRtree(geometries)
    result={}
    for row in range(grid.height):
        for col in range(grid.width):
            x,y=grid.center(row,col);point=Point(x,y)
            hits=[int(i) for i in tree.query(point) if geometries[int(i)].covers(point)]
            cell=box(x-grid.resolution/2,y-grid.resolution/2,x+grid.resolution/2,y+grid.resolution/2)
            # A center exactly on a state boundary has no unambiguous entitlement.
            state=features[hits[0]]['properties']['STUSPS'] if len(hits)==1 else 'unknown'
            boundary=len(hits)!=1 or not geometries[hits[0]].covers(cell)
            result[f'{row},{col}']={'state':state,'region':region_by_state.get(state,'unknown'),
                'boundary':boundary,'state_footprint_hash':expected_sha256,'region_registry_hash':digest(config)}
    return result


def grouped_permutation(predict,X,y,names,blocks,*,repeats=10,seed=42):
    X=np.asarray(X);y=np.asarray(y);b=np.asarray(blocks);rng=np.random.default_rng(seed)
    if tuple(names)!=tuple(f['name'] for f in load_registry()['features']):raise ValueError('importance requires registered features')
    base=predict(X);baseline={block:float(np.mean((base[b==block]-y[b==block])**2)) for block in sorted(set(b))}
    results={}
    for family in sorted({f['family'] for f in load_registry()['features']}):
        columns=[j for j,f in enumerate(load_registry()['features']) if f['family']==family];losses=[]
        for _ in range(repeats):
            altered=X.copy()
            for block in sorted(set(b)):
                idx=np.where(b==block)[0];shuffled=rng.permutation(idx)
                altered[np.ix_(idx,columns)]=X[np.ix_(shuffled,columns)]
            pred=predict(altered)
            losses.append(float(np.mean([np.mean((pred[b==block]-y[b==block])**2)-baseline[block] for block in baseline])))
        results[family]={'mean_brier_increase':float(np.mean(losses)),'permutation_repeats':losses,
            'recipe':'joint family shuffle within independent weather blocks; equal-block mean loss change',
            'limitation':'Correlated features can substitute; repetition variation is not a confidence interval or physical importance.'}
    return results


def transfer_experiments(candidate,dataset,splits,config,*,kind,test_mode=False):
    """Refit every selection/preprocessing/calibration/support stage per holdout.

    Expensive by design; run as a separate CPU job. Never consumes final-test rows.
    Ineligible folds are persisted, never filled with a production-looking score.
    """
    from .contracts import time
    from .train import fit_trees,raw_margins
    from .baselines import fit_baselines,baseline_predictions
    from .calibration import fit_calibration,choose_calibration,calibrated_array
    from .support import fit_support
    from .evaluate import metrics,candidate_hash
    if kind not in ('state','domain_id','block_id'):raise ValueError('unsupported holdout')
    development=role_indices(dataset,splits,('train','tune','calibrate','gate'));reports=[]
    for group in sorted({dataset.rows[i][kind] for i in development}):
        held=[i for i in development if dataset.rows[i][kind]==group and (kind=='block_id' or splits.assignments[dataset.features.keys[i].id]=='gate')]
        if not held:
            reports.append({'group':group,'eligible':False,'reason':'no later independent gate rows'});continue
        start=min(time(dataset.rows[i]['cutoff']) for i in held)
        assignments={k.id:role for k in dataset.features.keys if (role:=splits.assignments.get(k.id)) and role!='test'}
        for i,k in enumerate(dataset.features.keys):
            if dataset.rows[i][kind]==group or (kind=='block_id' and time(dataset.rows[i]['end'])+timedelta(hours=72)>start):assignments.pop(k.id,None)
        fold=splits.model_copy(update={'assignments':assignments})
        try:
            base=fit_baselines(dataset,fold,config,test_mode=test_mode)
            trees=fit_trees(dataset,fold,config,test_mode=test_mode)
            f=raw_margins(trees,dataset.features);regions=[r['region'] for r in dataset.rows]
            c=fit_calibration(f,dataset.labels,regions,fold,{'dataset':dataset,'test_mode':test_mode})
            gate=role_indices(dataset,fold,('gate',))
            selected=choose_calibration(c,f,dataset.labels,regions,dataset,fold) if len(gate) else {h:c[h]['global'] for h in HORIZONS}
            train=role_indices(dataset,fold,('train',))
            support=fit_support(subset_features(dataset.features,train),[dataset.rows[i]['block_id'] for i in train],{})
            pred=np.zeros(len(held))
            for h in HORIZONS:
                j=[j for j,i in enumerate(held) if dataset.features.keys[i].horizon_id==h]
                ii=[held[v] for v in j]
                pred[j]=calibrated_array(selected[h],f[ii],[regions[i] for i in ii])
            bp=baseline_predictions(base,dataset.features,dataset.rows)
            reports.append({'group':group,'eligible':True,'kind':kind,'rows':len(held),'metrics':metrics(dataset.labels[held],pred),
                'baselines':{n:metrics(dataset.labels[held],v[held]) for n,v in bp.items()},
                'excluded_from_all_fitting':True,'fit_row_ids_hash':digest([k for k,v in assignments.items() if v in ('train','tune','calibrate')]),
                'support_training_hashes':{h:ref['training_row_hash'] for h,ref in support['horizons'].items()},
                'fold_trials':list(trees.trials)})
        except (ValueError,DataReadinessError) as exc:reports.append({'group':group,'kind':kind,'eligible':False,'reason':str(exc)})
    return {'schema_version':'outage-development-audit/v1','candidate_hash':candidate_hash(candidate),
        'dataset_id':dataset.dataset_id,'split_hash':digest(splits),'kind':kind,'uses_final_test':False,'folds':reports,
        'eligible_folds':sum(r['eligible'] for r in reports)}


def generate_audits(candidate,dataset,splits,store,development_refs):
    """Create identity-bound evidence records from completed development jobs."""
    import lightgbm as lgb
    from scipy.special import expit
    from scipy.spatial import cKDTree
    from .storage import checked_read,put_bytes
    from .evaluate import candidate_hash
    from .splits import validate_splits
    from .explain import explain
    from raster_pipeline.contracts import canonical
    validate_splits(dataset,splits)
    if candidate.training['dataset_id']!=dataset.dataset_id or candidate.training['split_hash']!=digest(splits):raise ValueError('audit model/dataset/split identity mismatch')
    development=[]
    for ref in development_refs:
        report=json.loads(checked_read(store,ref))
        if report.get('schema_version')!='outage-development-audit/v1' or report.get('candidate_hash')!=candidate_hash(candidate) or report.get('dataset_id')!=dataset.dataset_id or report.get('split_hash')!=digest(splits) or report.get('uses_final_test') is not False:
            raise ValueError('incompatible development holdout report')
        development.append(report)
    geo=[r for r in development if r['kind'] in ('state','domain_id')];storms=[r for r in development if r['kind']=='block_id']
    results={};indices=role_indices(dataset,splits,('gate',));rng=np.random.default_rng(42)
    if not len(indices):raise DataReadinessError('gate rows required for explanation/importance audits')
    examples=[];diagnostics={};reconstructions=0
    for h in HORIZONS:
        idx=np.asarray([i for i in indices if dataset.features.keys[i].horizon_id==h],int)
        if not len(idx):raise DataReadinessError('all horizons required for development audits')
        sample=np.sort(rng.choice(idx,min(10000,len(idx)),replace=False));X=dataset.features.values[sample];y=dataset.labels[sample]
        model=lgb.Booster(model_str=candidate.trees[h]);columns=[candidate.feature_names.index(n) for n in model.feature_name()];cal=candidate.calibrations[h]
        def predict(a):return expit(cal.slope*model.predict(a[:,columns],raw_score=True,num_threads=4)+cal.intercept)
        importance=grouped_permutation(predict,X,y,candidate.feature_names,[dataset.rows[i]['block_id'] for i in sample])
        contributions=model.predict(X[:,columns],pred_contrib=True,num_threads=4);raw=model.predict(X[:,columns],raw_score=True,num_threads=4)
        if not np.allclose(contributions.sum(1),raw,atol=1e-6,rtol=0):raise ValueError('development contribution reconstruction failed')
        distributions={name:{'raw_quantiles':np.quantile(contributions[:,j],[0,.05,.5,.95,1]).tolist(),
            'mean_absolute_calibrated_log_odds':float(np.mean(np.abs(cal.slope*contributions[:,j])))} for j,name in enumerate(model.feature_name())}
        support=candidate.support['horizons'][h];reference=np.asarray(support['reference']);median=np.asarray(support['median']);scale=np.asarray(support['scale'])
        observed=reference*scale+median;base=observed[np.sort(rng.choice(len(observed),min(128,len(observed)),replace=False))]
        tree=cKDTree(reference);curves={}
        for name in ('wind_max_ms','rain_total_mm','temp_mean_c','canopy_fraction'):
            j=candidate.feature_names.index(name);curve=[]
            for value in np.unique(np.quantile(observed[:,j],np.linspace(0,1,11))):
                altered=base.copy();altered[:,j]=value;prob=predict(altered)
                valid=(altered>=np.asarray(support['min'])).all(1)&(altered<=np.asarray(support['max'])).all(1)
                valid&=tree.query((altered-median)/scale,k=1)[0]<=support['distance_cutoff']
                curve.append({'feature_value':float(value),'mean_model_probability':float(prob.mean()),'supported_fraction':float(valid.mean())})
            curves[name]=curve
        i=int(sample[0]);examples.append(explain(candidate,FeatureRow(key=dataset.features.keys[i],names=dataset.features.names,
            values=tuple(dataset.features.values[i]),region=dataset.rows[i]['region'],provenance=dataset.features.provenance,status='development_audit')))
        reconstructions+=len(sample)
        diagnostics[h]={'grouped_permutation':importance,'contribution_distributions':distributions,'response_curves':curves,
            'sample_rows':len(sample),'row_ids_hash':digest([dataset.features.keys[i].id for i in sample]),
            'response_interpretation':'Partial dependence associations with zero regional offset; correlated-feature interventions may be unsupported.'}
    def record(kind,passed,result,**extra):
        return {'schema_version':'outage-audit/v1','kind':kind,'candidate_hash':candidate_hash(candidate),
            'dataset_id':dataset.dataset_id,'split_hash':digest(splits),'uses_final_test':False,'passed':bool(passed),
            'results':result,'fixture':candidate.fixture,**extra}
    results['lineage_audit']=record('lineage_audit',dataset.lineage.get('verified_labels') and dataset.features.eligible.all() and
        (candidate.fixture or dataset.features.provenance.get('normalization_reproducible')),
        {'lineage':dataset.lineage,'split_reconstructed':True,'raw_archive_required':not candidate.fixture,
            'external_semantics':'Human-reviewed source/coverage evidence remains required; software checks do not establish scientific truth.'})
    results['explanation_audit']=record('explanation_audit',True,{'reconstructed_rows':reconstructions,'representative_explanations':examples})
    for name,reports in [('geographic_holdouts',geo),('storm_holdouts',storms)]:
        eligible=sum(sum(f['eligible'] for f in r['folds']) for r in reports)
        complete_kinds={r['kind'] for r in reports}
        passed=eligible>=2 and (complete_kinds=={'state','domain_id'} if name=='geographic_holdouts' else complete_kinds=={'block_id'})
        results[name]=record(name,passed,{'reports':reports,'meaning':'Completed independent experiments, including negative skill; not national-transfer approval.'},eligible_folds=eligible)
    results['grouped_importance']=record('grouped_importance',True,diagnostics)
    return {name:put_bytes(store,'outage/v1/audits/complete',canonical(report),'json') for name,report in results.items()}
