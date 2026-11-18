"""Portable native model artifacts; strict schema, identity and checksum validation."""
import hashlib
import json
import numpy as np
import lightgbm as lgb
from raster_pipeline.contracts import canonical,digest
from .contracts import ModelBundle, ModelRef, HORIZONS
from .features import load_registry
from .storage import checked_read
from .evaluate import candidate_hash


def assemble_bundle(trees,baselines,calibrations,support,dataset):
    bundle=ModelBundle(model_id='pending',feature_names=trees.feature_names,feature_registry_hash=digest(load_registry()),
        grid_id=dataset.features.keys[0].grid_id,static_version=dataset.features.provenance['static_version'],
        canopy_vintage=str(dataset.features.provenance['canopy_vintage']),trees=trees.models,calibrations=calibrations,
        baselines=baselines.model_dump(mode='json'),support=support,fixture=dataset.features.fixture,
        training={**trees.training,'trials':list(trees.trials),'records':trees.records,'lineage':dataset.lineage})
    return bundle.model_copy(update={'model_id':candidate_hash(bundle)})


def validate_bundle(bundle,*,test_mode=False):
    registry=load_registry();names=tuple(f['name'] for f in registry['features'])
    if bundle.fixture!=test_mode:raise ValueError('fixture/live model mixing forbidden')
    if bundle.model_id!=candidate_hash(bundle):raise ValueError('model identity mismatch')
    if bundle.feature_names!=names or bundle.feature_registry_hash!=digest(registry):raise ValueError('model feature registry/order mismatch')
    if set(bundle.trees)!=set(HORIZONS) or set(bundle.calibrations)!=set(HORIZONS):raise ValueError('all three models/calibrators required')
    if bundle.support['feature_names']!=list(names) or set(bundle.support['horizons'])!=set(HORIZONS):raise ValueError('support schema mismatch')
    for h,text in bundle.trees.items():
        model=lgb.Booster(model_str=text);selected=model.feature_name()
        if not selected or len(set(selected))!=len(selected) or selected!=[n for n in names if n in selected]:raise ValueError('native model feature schema/order mismatch')
        if model.num_trees()!=bundle.training['records'][h]['tree_count']:raise ValueError('native tree count mismatch')
        if model.dump_model().get('objective','').split()[0]!='binary':raise ValueError('binary models required')
        cal=bundle.calibrations[h]
        if not np.isfinite([cal.slope,cal.intercept,*cal.offsets.values()]).all() or abs(sum(cal.offsets.values()))>1e-6:
            raise ValueError('invalid calibration values/offset constraint')
    if bundle.evidence:
        e=bundle.evidence
        if e.fixture!=test_mode or e.candidate_hash!=candidate_hash(bundle) or e.dataset_id!=bundle.training['dataset_id'] or e.split_hash!=bundle.training['split_hash']:raise ValueError('release evidence model identity mismatch')
    return bundle


def save_bundle(bundle,store) -> ModelRef:
    validate_bundle(bundle,test_mode=bundle.fixture)
    prefix=f'outage/v1/models/{bundle.model_id}';references={}
    for h,text in bundle.trees.items():
        raw=text.encode();key=f'{prefix}/{h}.txt';store.put(key,raw)
        references[h]={'key':key,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        store.put(f'{prefix}/{h}-trees.json',canonical(lgb.Booster(model_str=text).dump_model()))
    manifest={'schema_version':'outage-model-artifact/v1','bundle':bundle.model_dump(mode='json',exclude={'trees'}),'tree_objects':references}
    # An evidence-bearing promotion is a new immutable manifest for the same
    # frozen candidate, rather than overwriting its pre-evaluation bundle.
    raw=canonical(manifest);sha=hashlib.sha256(raw).hexdigest();key=f'{prefix}/manifests/{sha}.json';store.put(key,raw)
    return ModelRef(model_id=bundle.model_id,manifest_key=key,sha256=sha)


def load_bundle(ref,store,*,test_mode=False) -> ModelBundle:
    m=json.loads(checked_read(store,{'key':ref.manifest_key,'sha256':ref.sha256}))
    if m['schema_version']!='outage-model-artifact/v1':raise ValueError('incompatible artifact schema')
    trees={h:checked_read(store,obj).decode() for h,obj in m['tree_objects'].items()}
    bundle=ModelBundle.model_validate({**m['bundle'],'trees':trees})
    if bundle.model_id!=ref.model_id:raise ValueError('model ref identity mismatch')
    return validate_bundle(bundle,test_mode=test_mode)
