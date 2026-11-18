"""Evidence inventories; a Parquet card alone never establishes training readiness."""
import hashlib
import json
from .storage import tree_bytes
from .contracts import SplitManifest


def readiness(store,budget_root,budget_gb=5):
    from .splits import load_dataset,validate_splits
    archives=[]
    for key in store.keys('outage/v1/archive/manifests'):
        raw,_=store.read(key);a=json.loads(raw)
        if not a['fixture']:archives.append({'archive_key':key,'cutoff':a['upstream']['cutoff'],
            'normalization_reproducible':a['normalization_reproducible']})
    live_sources=[]
    for key in store.keys('outage/v1/sources/california/snapshots'):
        raw,_=store.read(key);s=json.loads(raw)
        if not s['fixture']:live_sources.append(s)
    cards=[];splits=[];released=[]
    for key in store.keys('outage/v1/datasets'):
        if not key.endswith('.json'):continue
        raw,_=store.read(key);d=json.loads(raw)
        if not isinstance(d,dict):continue
        if d.get('schema_version')=='outage-dataset/v1' and not d['lineage'].get('fixture'):
            cards.append((d,{'key':key,'sha256':hashlib.sha256(raw).hexdigest()}))
        elif d.get('schema_version')=='outage-splits/v1':splits.append(SplitManifest.model_validate(d))
    datasets=[]
    for card,ref in cards:
        reasons=[];valid=False
        try:
            dataset=load_dataset(store,ref)
            if not dataset.features.provenance.get('normalization_reproducible'):reasons.append('incomplete_raw_normalization_archive')
            matching=[s for s in splits if s.dataset_id==dataset.dataset_id]
            if not matching:reasons.append('no_verified_five_role_split')
            for split in matching:
                validate_splits(dataset,split);valid=True
        except (ValueError,KeyError) as exc:reasons.append(str(exc))
        datasets.append({'dataset_id':card['dataset_id'],'rows':card['rows'],'independent_blocks':card['independent_blocks'],
            'ready':valid and not reasons,'reasons':reasons})
    for key in store.keys('outage/v1/evaluations'):
        if not key.endswith('.json'):continue
        raw,_=store.read(key);e=json.loads(raw)
        if e.get('schema_version')=='outage-release/v1' and not e['fixture'] and e['role']=='test' and e['passed']:released.append(key)
    return {'schema_version':'outage-readiness/v1','local_bytes':tree_bytes(budget_root),
        'preferred_budget_bytes':5_000_000_000,'configured_budget_bytes':int(budget_gb*1e9),'hard_limit_bytes':10_000_000_000,
        'live_weather_archives':archives,'live_source_snapshots':len(live_sources),'real_datasets':datasets,
        'ready_for_training':any(d['ready'] for d in datasets),'ready_for_probability_release':bool(released),
        'validated_release_reports':released,
        'limitations':['Current California map is prospective, not historical training data.',
            'Complete weather software does not establish a complete live forecast/static archive.',
            'No national-transfer entitlement follows from California coverage.',
            'Storage growth stops at budget; place durable forecast history in a configured remote store.']}
