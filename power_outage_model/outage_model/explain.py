"""Complete additive reconstruction in raw/calibrated log-odds units."""
import numpy as np
import lightgbm as lgb
from raster_pipeline.contracts import digest
from .features import load_registry
from .calibration import calibrate


def explain(bundle,row):
    if row.names!=bundle.feature_names or len(row.values)!=len(row.names):raise ValueError('explanation feature schema/order mismatch')
    if not np.isfinite(row.values).all():raise ValueError('invalid explanation inputs')
    model=lgb.Booster(model_str=bundle.trees[row.key.horizon_id])
    columns=[row.names.index(n) for n in model.feature_name()]
    X=np.asarray(row.values)[columns][None,:]
    margin=float(model.predict(X,raw_score=True,num_iteration=model.current_iteration(),num_threads=1)[0])
    native=model.predict(X,pred_contrib=True,num_iteration=model.current_iteration(),num_threads=1)[0]
    base=float(native[-1]);raw={name:0. for name in row.names}
    raw.update({name:float(v) for name,v in zip(model.feature_name(),native[:-1])})
    if not np.isclose(base+sum(raw.values()),margin,atol=1e-6,rtol=0):raise ValueError('raw explanation reconstruction failed')
    calibration=bundle.calibrations[row.key.horizon_id];score=calibrate(calibration,margin,row.region)
    calibrated={n:calibration.slope*v for n,v in raw.items()};calibrated_base=calibration.slope*base
    if not np.isclose(calibrated_base+sum(calibrated.values())+calibration.intercept+score['regional_offset'],score['calibrated_log_odds'],atol=1e-6,rtol=0):
        raise ValueError('calibrated explanation reconstruction failed')
    families={}
    for feature in load_registry()['features']:
        family=feature['family'];families[family]=families.get(family,0.)+calibrated[feature['name']]
    result={'schema_version':'outage-explanation/v1','row_key':row.key.model_dump(mode='json'),
        'feature_values':dict(zip(row.names,row.values)),'provenance':row.provenance,
        'reference':'LightGBM native expected raw-margin reference; tree-dependent background',
        'base_raw_margin':base,'raw_contributions':raw,'raw_margin':margin,'calibration_slope':calibration.slope,
        'calibration_intercept':calibration.intercept,'regional_offset':score['regional_offset'],
        'regional_fallback':score['regional_fallback'],'calibrated_base':calibrated_base,
        'calibrated_contributions':calibrated,'family_contributions':families,
        'calibrated_log_odds':score['calibrated_log_odds'],'probability':score['probability'],
        'contribution_unit':'log_odds','status':row.status,'model_id':bundle.model_id,
        'model_tree_sha256':__import__('hashlib').sha256(bundle.trees[row.key.horizon_id].encode()).hexdigest(),
        'interpretation':'Model associations relative to the named reference; not evidence of causation.'}
    return {**result,'sha256':digest(result)}
