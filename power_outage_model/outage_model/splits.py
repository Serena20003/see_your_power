"""Content-addressed Parquet datasets and purged five-role evaluation manifests."""
from collections import defaultdict, Counter
from datetime import timedelta
import io
import json
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from raster_pipeline.contracts import canonical, digest
from .contracts import (DatasetSnapshot, DataReadinessError, FeatureTable, RowKey,
    SplitManifest, ROLES, HORIZONS, time)
from .storage import checked_read, put_bytes


def subset_features(features,indices):
    indices=list(indices)
    return FeatureTable(names=features.names,values=features.values[indices].copy(),keys=tuple(features.keys[i] for i in indices),
        eligible=features.eligible[indices].copy(),metadata=tuple(features.metadata[i] for i in indices),
        provenance=features.provenance,fixture=features.fixture)


def assemble_dataset(labels,features,freeze_time):
    freeze_time=time(freeze_time)
    if not features.keys: raise DataReadinessError('no weather-feature history supplied')
    if not features.fixture and not features.provenance.get('normalization_reproducible'):
        raise DataReadinessError('complete raw normalization archive required for live training')
    lookup={x.key.id:x for x in labels}
    if len(lookup)!=len(labels): raise ValueError('duplicate label rows')
    indices=[]; rows=[]; y=[]; exclusions=[]
    for i,key in enumerate(features.keys):
        label=lookup.get(key.id); reason=None
        if label is None:reason='missing_label'
        elif label.freeze_time!=freeze_time:reason='label_freeze_mismatch'
        elif label.label is None:reason=label.exclusion_reason or 'null_label'
        elif label.mature_at>freeze_time:reason='immature_label'
        elif not label.coverage_ids:reason='missing_coverage_evidence'
        elif time(features.metadata[i]['start'])!=label.start or time(features.metadata[i]['end'])!=label.end:
            reason='feature_label_window_mismatch'
        if not features.eligible[i]:reason='ineligible_features'
        if features.metadata[i].get('retrospective_only'):reason='retrospective_static_vintage'
        if reason:
            exclusions.append({'row_id':key.id,'reason':reason}); continue
        if not label.block_id or not label.state or not label.domain_id or label.domain_id=='unknown':
            raise DataReadinessError('reviewed event blocks and reporting-domain metadata required')
        indices.append(i);y.append(label.label)
        rows.append({**features.metadata[i],**label.model_dump(mode='json',exclude={'key','label'}),
                     'horizon_id':key.horizon_id})
    if not indices:raise DataReadinessError('no mature, complete, aligned real training rows')
    f=subset_features(features,indices)
    lineage={'features':features.provenance,'labels_hash':digest([x.model_dump(mode='json') for x in labels]),
        'verified_labels':True,'fixture':features.fixture}
    identity={'row_ids':[k.id for k in f.keys],'labels':y,'lineage':lineage,'freeze_time':freeze_time.isoformat()}
    return DatasetSnapshot(dataset_id=digest(identity),features=f,labels=np.asarray(y,dtype='int8'),rows=tuple(rows),
        exclusions=tuple(exclusions),freeze_time=freeze_time,lineage=lineage)


def feature_bytes(features):
    columns={n:pa.array(features.values[:,j]) for j,n in enumerate(features.names)}
    columns.update({'row_key_json':pa.array([canonical(k).decode() for k in features.keys]),
        'eligible':pa.array(features.eligible),'metadata_json':pa.array([canonical(m).decode() for m in features.metadata])})
    table=pa.table(columns)
    meta={'names':list(features.names),'provenance':features.provenance,'fixture':features.fixture}
    table=table.replace_schema_metadata({b'outage-features/v1':canonical(meta)})
    out=io.BytesIO();pq.write_table(table,out,compression='zstd',row_group_size=65536)
    return out.getvalue()


def features_from_bytes(raw):
    table=pq.read_table(io.BytesIO(raw));meta=json.loads(table.schema.metadata[b'outage-features/v1'])
    names=tuple(meta['names'])
    if tuple(table.column_names[:len(names)])!=names:raise ValueError('Parquet feature order mismatch')
    return FeatureTable(names=names,values=np.column_stack([table[n].to_numpy() for n in names]),
        keys=tuple(RowKey.model_validate_json(s) for s in table['row_key_json'].to_pylist()),
        eligible=table['eligible'].to_numpy(),metadata=tuple(json.loads(s) for s in table['metadata_json'].to_pylist()),
        provenance=meta['provenance'],fixture=meta['fixture'])


def save_dataset(dataset,store):
    prefix=f'outage/v1/datasets/{dataset.dataset_id}'
    feature_ref=put_bytes(store,prefix,feature_bytes(dataset.features),'parquet')
    table=pa.table({'label':pa.array(dataset.labels),'row_json':pa.array([canonical(r).decode() for r in dataset.rows])})
    out=io.BytesIO();pq.write_table(table,out,compression='zstd')
    label_ref=put_bytes(store,prefix,out.getvalue(),'parquet')
    exclusion_ref=put_bytes(store,prefix,canonical(dataset.exclusions),'json')
    card={'schema_version':'outage-dataset/v1','dataset_id':dataset.dataset_id,'features':feature_ref,'labels':label_ref,
          'exclusions':exclusion_ref,'freeze_time':dataset.freeze_time.isoformat(),'lineage':dataset.lineage,
          'rows':len(dataset.rows),'positive_rows':int(dataset.labels.sum()),
          'independent_blocks':len({r['block_id'] for r in dataset.rows}),
          'states':sorted({r['state'] for r in dataset.rows}),
          'event_count':len({e for r in dataset.rows for e in r['event_ids']}),
          'limits':'Evaluated reporting cells only; no imputed labels or unobserved geographic entitlement.'}
    return put_bytes(store,prefix,canonical(card),'json')


def load_dataset(store,ref,*,test_mode=False):
    card=json.loads(checked_read(store,ref))
    f=features_from_bytes(checked_read(store,card['features']))
    if f.fixture!=test_mode:raise ValueError('fixture/live dataset mixing forbidden')
    table=pq.read_table(io.BytesIO(checked_read(store,card['labels'])))
    d=DatasetSnapshot(dataset_id=card['dataset_id'],features=f,labels=table['label'].to_numpy(),
        rows=tuple(json.loads(s) for s in table['row_json'].to_pylist()),exclusions=tuple(json.loads(checked_read(store,card['exclusions']))),
        freeze_time=time(card['freeze_time']),lineage=card['lineage'])
    if len(d.rows)!=len(d.labels) or len(d.rows)!=len(d.features.keys) or not np.isin(d.labels,[0,1]).all():
        raise ValueError('invalid dataset rows/labels')
    if not test_mode:validate_dataset_lineage(d,store)
    return d


def make_splits(dataset,policy):
    if set(policy.boundaries)!=set(ROLES) or not policy.reviewed_blocks_hash:
        raise DataReadinessError('all five exact role boundaries and reviewed event-block hash required')
    bounds={role:tuple(time(t) for t in policy.boundaries[role]) for role in ROLES}
    previous=None
    for role in ROLES:
        lo,hi=bounds[role]
        if hi<=lo or (previous and lo<previous):raise ValueError('overlapping/unordered chronological roles')
        previous=hi
    assignments={}; exclusions={}; membership=defaultdict(set); event_membership=defaultdict(set)
    for i,(key,row) in enumerate(zip(dataset.features.keys,dataset.rows)):
        cutoff=time(row['cutoff']); end=time(row['end'])
        eligible=[]
        for n,role in enumerate(ROLES):
            lo,hi=bounds[role]
            # For adjacent supplied blocks purge the first 72h of the later block.
            lower=lo if n==0 else max(lo,bounds[ROLES[n-1]][1]+timedelta(hours=policy.embargo_hours))
            if lower<=cutoff and end<=hi:eligible.append(role)
        if len(eligible)!=1:
            exclusions[key.id]='crossing_role_boundary_or_embargo';continue
        role=eligible[0]
        if role in ('train','tune','calibrate') and (row['state'] in policy.held_out_states or row['domain_id'] in policy.held_out_domains):
            exclusions[key.id]='geographic_holdout';continue
        assignments[key.id]=role
        membership[row['block_id']].add(role)
        for e in row['event_ids']:event_membership[e].add(role)
    bad_blocks={b for b,v in membership.items() if len(v)>1}
    bad_events={e for e,v in event_membership.items() if len(v)>1}
    for key,row in zip(dataset.features.keys,dataset.rows):
        if key.id in assignments and (row['block_id'] in bad_blocks or set(row['event_ids'])&bad_events):
            assignments.pop(key.id);exclusions[key.id]='shared_storm_or_onset_across_roles'
    split=SplitManifest(dataset_id=dataset.dataset_id,policy=policy,assignments=assignments,exclusions=exclusions)
    for role in ROLES:
        for horizon in HORIZONS:
            idx=role_indices(dataset,split,(role,),horizon)
            if len(idx)<2 or len(np.unique(dataset.labels[idx]))!=2:
                raise DataReadinessError(f'insufficient independent two-class history for {role}/{horizon}')
    return split


def role_indices(dataset,splits,roles,horizon=None):
    if splits.dataset_id!=dataset.dataset_id:raise ValueError('split dataset mismatch')
    return np.asarray([i for i,key in enumerate(dataset.features.keys) if splits.assignments.get(key.id) in roles
        and (horizon is None or key.horizon_id==horizon)],dtype=int)


def development_folds(dataset,splits,kind):
    """Entire geography/storm held out from every fitting/calibration reference."""
    if kind not in ('state','domain_id','block_id'):raise ValueError('unknown holdout kind')
    development=role_indices(dataset,splits,('train','tune','calibrate','gate'))
    for group in sorted({dataset.rows[i][kind] for i in development}):
        fit=[i for i in development if dataset.rows[i][kind]!=group]
        held=[i for i in development if dataset.rows[i][kind]==group]
        yield group,np.asarray(fit,dtype=int),np.asarray(held,dtype=int)


def validate_splits(dataset,splits):
    rebuilt=make_splits(dataset,splits.policy)
    if rebuilt!=splits:raise ValueError('split assignments differ from reviewed policy and dataset')
    return True


def validate_dataset_lineage(dataset,store):
    """Recheck durable raw/normalized closure at live training admission."""
    refs=dataset.features.provenance.get('archive_refs',[])
    if not refs:raise DataReadinessError('dataset lacks durable archive references')
    verified=set()
    for ref in refs:
        archive=json.loads(checked_read(store,{'key':ref['manifest_key'],'sha256':ref['sha256']}))
        if digest(archive)!=ref['archive_id'] or archive.get('fixture') or not archive.get('normalization_reproducible'):
            raise DataReadinessError('dataset archive is incomplete, incompatible or fixture data')
        objects=list(archive['remapping'].values())+archive.get('raw_inputs',[])+archive.get('normalization_assets',[])
        for obj in objects:
            identity=(obj['key'],obj['sha256'])
            if identity not in verified:checked_read(store,obj);verified.add(identity)
    for ref in dataset.features.provenance.get('source_refs',[]):checked_read(store,ref)
    for name in ('coverage_ref','metadata_ref'):
        ref=dataset.features.provenance.get(name)
        if not ref:raise DataReadinessError('missing source/coverage lineage reference')
        checked_read(store,ref)
    return True
