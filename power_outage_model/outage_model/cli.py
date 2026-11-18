"""Explicit one-shot jobs. No synthetic data generator or implicit runtime fitting."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import numpy as np
from raster_pipeline.contracts import canonical,digest
from raster_pipeline.storage import open_store,LocalStore
from .contracts import (ArchiveRef,ModelRef,PredictionBatch,SplitPolicy,SplitManifest,LabelPolicy,
    CoverageInterval,OutageEvent,RawSnapshot,ReleaseEvidence,FeatureRow,DataReadinessError,time)
from .storage import BudgetStore,tree_bytes,checked_read,put_bytes

ROOT=Path(__file__).resolve().parents[1]


def read_file(path):return json.loads(Path(path).read_text())


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--store',required=True,help='Local object-store directory or gs://bucket root')
    p.add_argument('--budget-root',default=str(ROOT),help='Root containing all local model files, environments and caches')
    p.add_argument('--budget-gb',type=float,default=5.,help='Preferred 5 GB; hard maximum 10 GB')
    p.add_argument('--test-mode',action='store_true',help='Explicit fixture-only isolation; never production release')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('readiness')
    a=sub.add_parser('make-audits');a.add_argument('--model-ref',required=True);a.add_argument('--dataset-ref',required=True);a.add_argument('--splits',required=True);a.add_argument('--development-refs',required=True)
    a=sub.add_parser('audit-development');a.add_argument('--model-ref',required=True);a.add_argument('--dataset-ref',required=True);a.add_argument('--splits',required=True);a.add_argument('--kind',choices=['state','domain_id','block_id'],required=True)
    a=sub.add_parser('collect');a.add_argument('--source-policy',help='Reviewed California mapping policy; defaults to no approved semantics')
    a=sub.add_parser('archive-weather');a.add_argument('--manifest-key',required=True);a.add_argument('--raw-root');a.add_argument('--normalization-assets',help='JSON list of native geometry/weight asset refs or verified paths')
    a=sub.add_parser('build-dataset');a.add_argument('--spec',required=True);a.add_argument('--freeze-time',required=True)
    a=sub.add_parser('split');a.add_argument('--dataset-ref',required=True);a.add_argument('--policy',required=True)
    a=sub.add_parser('train');a.add_argument('--dataset-ref',required=True);a.add_argument('--splits',required=True)
    a=sub.add_parser('evaluate');a.add_argument('--model-ref',required=True);a.add_argument('--dataset-ref',required=True)
    a.add_argument('--splits',required=True);a.add_argument('--evaluation-spec',required=True);a.add_argument('--role',choices=['gate','test'],required=True)
    a=sub.add_parser('score');a.add_argument('--archive-ref',required=True);a.add_argument('--model-ref',required=True)
    a.add_argument('--publish',action='store_true',help='Advance separate outage pointer after full validation')
    a=sub.add_parser('explain');a.add_argument('--batch-key',required=True);a.add_argument('--row-id',required=True)
    a=sub.add_parser('validate-batch');a.add_argument('--batch-key',required=True)
    a=sub.add_parser('inspect');a.add_argument('--key',required=True)
    a=sub.add_parser('rollback');a.add_argument('--expected-generation',required=True)
    return p


def _dataset(args,store):
    from .splits import load_dataset
    return load_dataset(store,read_file(args.dataset_ref),test_mode=args.test_mode)


def _build_dataset(args,store):
    from .archive import load_archive
    from .features import build_features
    from .labels import reconcile_events,build_labels,boundary_sensitivity
    from .splits import assemble_dataset,save_dataset,subset_features
    from .contracts import FeatureTable
    spec=read_file(args.spec);policy=LabelPolicy.model_validate(spec['label_policy'])
    if not spec.get('event_snapshot_refs') or not spec.get('coverage_ref') or not spec.get('row_metadata_ref'):
        raise DataReadinessError('real source snapshots, coverage and reviewed geographic/event-block metadata required')
    snapshots=[]
    for ref in spec['event_snapshot_refs']:
        record=json.loads(checked_read(store,ref))
        if record['fixture']!=args.test_mode:raise ValueError('fixture/live event mixing forbidden')
        if time(record['source_snapshot']['retrieved_at'])>time(args.freeze_time):raise ValueError('source snapshot arrived after dataset freeze')
        snapshots.append([OutageEvent.model_validate(e) for e in record['events']])
    ledger=reconcile_events(snapshots,policy)
    coverage=[CoverageInterval.model_validate(c) for c in json.loads(checked_read(store,spec['coverage_ref']))]
    meta=json.loads(checked_read(store,spec['row_metadata_ref']))
    all_features=[];labels=[]
    for ref in spec['archives']:
        archive=load_archive(store,ArchiveRef.model_validate(ref),test_mode=args.test_mode)
        f=build_features(archive)
        selected={k.id:meta[k.id] for k in f.keys if k.id in meta}
        # Metadata may omit unsupported cells: retain them as null labels with
        # explicit absent coverage and never fabricate geography or storm IDs.
        keys=[k for k in f.keys if k.id in selected]
        labels.extend(build_labels(ledger,coverage,keys,time(args.freeze_time),policy,grid=archive.grid,
            windows={archive.ref.archive_id:archive.manifest['target_start']},row_metadata=selected))
        reviewed_indices=[i for i,k in enumerate(f.keys) if k.id in selected]
        part=subset_features(f,reviewed_indices)
        for i in reviewed_indices:
            if meta[f.keys[i].id]['season']!=f.metadata[i]['season']:raise ValueError('reviewed season differs from UTC window midpoint')
        part=part.model_copy(update={'metadata':tuple({**f.metadata[i],**{k:v for k,v in meta[f.keys[i].id].items() if k in ('state','region','block_id','spatial_boundary')}} for i in reviewed_indices)})
        all_features.append(part)
        if sum(len(t.keys) for t in all_features)>1_000_000:
            raise DataReadinessError('dataset exceeds the bounded CPU job row budget; use reviewed temporal partitions without random pixel sampling')
    if not all_features or not any(f.keys for f in all_features):raise DataReadinessError('no real forecast archives supplied')
    first=all_features[0]
    for f in all_features:
        if f.names!=first.names or f.fixture!=first.fixture or f.provenance['static_version']!=first.provenance['static_version']:
            raise ValueError('incompatible feature/static snapshots; create an explicitly versioned dataset')
    provenance={**first.provenance,'archive_ids':[f.provenance['archive_id'] for f in all_features],'archive_refs':spec['archives'],
        'normalization_reproducible':all(f.provenance['normalization_reproducible'] for f in all_features),
        'metadata_ref':spec['row_metadata_ref'],'coverage_ref':spec['coverage_ref'],'source_refs':spec['event_snapshot_refs']}
    features=FeatureTable(names=first.names,values=np.concatenate([f.values for f in all_features]),
        keys=tuple(k for f in all_features for k in f.keys),eligible=np.concatenate([f.eligible for f in all_features]),
        metadata=tuple(m for f in all_features for m in f.metadata),fixture=first.fixture,provenance=provenance)
    dataset=assemble_dataset(labels,features,time(args.freeze_time))
    ledger_ref=put_bytes(store,'outage/v1/audits/labels',canonical(ledger),'json')
    sensitivity_ref=put_bytes(store,'outage/v1/audits/labels',canonical(boundary_sensitivity(ledger.events,archive.grid)),'json')
    dataset=dataset.model_copy(update={'lineage':{**dataset.lineage,'event_ledger_ref':ledger_ref,'location_sensitivity_ref':sensitivity_ref,
        'reviewed_metadata_ref':spec['row_metadata_ref']}})
    return save_dataset(dataset,store)


def run(args,store):
    if args.command=='readiness':
        from .readiness import readiness
        return readiness(store,args.budget_root,args.budget_gb)
    if args.command=='make-audits':
        from .artifacts import load_bundle
        from .audits import generate_audits
        bundle=load_bundle(ModelRef.model_validate(read_file(args.model_ref)),store,test_mode=args.test_mode)
        return generate_audits(bundle,_dataset(args,store),SplitManifest.model_validate(read_file(args.splits)),store,read_file(args.development_refs))
    if args.command=='audit-development':
        from .artifacts import load_bundle
        from .audits import transfer_experiments
        from .train import load_train_config
        bundle=load_bundle(ModelRef.model_validate(read_file(args.model_ref)),store,test_mode=args.test_mode)
        report=transfer_experiments(bundle,_dataset(args,store),SplitManifest.model_validate(read_file(args.splits)),load_train_config(),kind=args.kind,test_mode=args.test_mode)
        return put_bytes(store,'outage/v1/audits/development',canonical(report),'json')
    if args.command=='collect':
        import httpx
        from .adapters.california import CaliforniaAdapter
        adapter=CaliforniaAdapter(store,policy=read_file(args.source_policy) if args.source_policy else None,test_mode=args.test_mode)
        with httpx.Client(follow_redirects=True) as client:snapshot=adapter.fetch_snapshot(client,datetime.now(timezone.utc))
        events=adapter.normalize(snapshot)
        ref=put_bytes(store,'outage/v1/sources/california/events',canonical({'fixture':args.test_mode,
            'snapshot_id':snapshot.snapshot_id,'source_snapshot':snapshot.model_dump(mode='json'),'events':[e.model_dump(mode='json') for e in events]}),'json')
        return {'snapshot':snapshot.model_dump(mode='json'),'events_ref':ref,'coverage_intervals':[],
                'training_eligible':False,'reason':'prospective collection only; source audits required'}
    if args.command=='archive-weather':
        from .archive import archive_batch
        return archive_batch(store,args.manifest_key,test_mode=args.test_mode,raw_root=args.raw_root,normalization_assets=read_file(args.normalization_assets) if args.normalization_assets else None).model_dump(mode='json')
    if args.command=='build-dataset':return _build_dataset(args,store)
    if args.command=='split':
        from .splits import make_splits
        dataset=_dataset(args,store);split=make_splits(dataset,SplitPolicy.model_validate(read_file(args.policy)))
        return {'split_ref':put_bytes(store,f'outage/v1/datasets/{dataset.dataset_id}',canonical(split),'json'),
            'splits':split.model_dump(mode='json')}
    if args.command=='train':
        from .splits import role_indices,subset_features,validate_splits
        from .baselines import fit_baselines
        from .train import fit_trees,raw_margins,load_train_config
        from .calibration import fit_calibration,choose_calibration
        from .support import fit_support
        from .artifacts import assemble_bundle,save_bundle
        dataset=_dataset(args,store);splits=SplitManifest.model_validate(read_file(args.splits));validate_splits(dataset,splits);config=load_train_config()
        baseline=fit_baselines(dataset,splits,config,test_mode=args.test_mode)
        trees=fit_trees(dataset,splits,config,test_mode=args.test_mode)
        margins=raw_margins(trees,dataset.features);regions=[r['region'] for r in dataset.rows]
        candidates=fit_calibration(margins,dataset.labels,regions,splits,{'dataset':dataset,'test_mode':args.test_mode})
        calibration=choose_calibration(candidates,margins,dataset.labels,regions,dataset,splits)
        train=role_indices(dataset,splits,('train',))
        support=fit_support(subset_features(dataset.features,train),[dataset.rows[i]['block_id'] for i in train],{})
        trees=trees.model_copy(update={'training':{**trees.training,'calibration_trials':{h:{k:v.model_dump(mode='json') for k,v in fits.items()} for h,fits in candidates.items()}}})
        bundle=assemble_bundle(trees,baseline,calibration,support,dataset)
        return {'model_ref':save_bundle(bundle,store).model_dump(mode='json'),'released':False,
                'next':'freeze scopes and development audits, evaluate gate, then untouched final test'}
    if args.command=='evaluate':
        from .artifacts import load_bundle,save_bundle
        from .baselines import BaselineBundle
        from .evaluate import evaluate,load_release_config,candidate_hash
        dataset=_dataset(args,store);split=SplitManifest.model_validate(read_file(args.splits))
        bundle=load_bundle(ModelRef.model_validate(read_file(args.model_ref)),store,test_mode=args.test_mode)
        spec=read_file(args.evaluation_spec);policy=load_release_config();gate=None
        # Every audit reference must resolve to checksum-verified persisted content.
        audit_reports={name:json.loads(checked_read(store,ref)) for name,ref in spec.get('audit_refs',{}).items()}
        from .evaluate import validate_audit_reports
        validate_audit_reports(audit_reports,bundle,dataset,split)
        if args.role=='test':
            gate=ReleaseEvidence.model_validate_json(checked_read(store,spec['gate_evidence_ref']))
            if gate.role!='gate' or gate.dataset_id!=dataset.dataset_id or gate.split_hash!=digest(split) or gate.policy_hash!=digest(policy) or gate.candidate_hash!=candidate_hash(bundle):raise ValueError('frozen gate/candidate/dataset/split mismatch')
            if not any(s.get('passed') for s in gate.scopes):raise DataReadinessError('no approved gate scopes; final test remains untouched')
            claim={'dataset_id':dataset.dataset_id,'candidate_hash':candidate_hash(bundle),'policy_hash':digest(policy),
                'split_hash':digest(split),'gate_decision_hash':digest(gate),
                'test_start':time(split.policy.boundaries['test'][0]).isoformat(),
                'test_end':time(split.policy.boundaries['test'][1]).isoformat()}
            from .evaluate import consume_final_period
            consume_final_period(store,claim,test_mode=args.test_mode)
        evidence=evaluate(bundle,BaselineBundle.model_validate(bundle.baselines),dataset,split,
            {'release_policy':policy,'scopes':spec.get('scopes',[]),'audit_refs':spec.get('audit_refs',{}),'gate_evidence':gate,'audit_reports':audit_reports},role=args.role)
        ref=put_bytes(store,f'outage/v1/evaluations/{dataset.dataset_id}',canonical(evidence),'json')
        if args.role=='test':bundle=bundle.model_copy(update={'evidence':evidence})
        return {'evidence_ref':ref,'evidence':evidence.model_dump(mode='json'),
                'model_ref':save_bundle(bundle,store).model_dump(mode='json')}
    if args.command=='score':
        from .archive import load_archive
        from .artifacts import load_bundle
        from .predict import predict_batch
        from .publish import publish_batch,LATEST,LATEST_TEST
        archive=load_archive(store,ArchiveRef.model_validate(read_file(args.archive_ref)),test_mode=args.test_mode)
        model=load_bundle(ModelRef.model_validate(read_file(args.model_ref)),store,test_mode=args.test_mode)
        _,generation=store.read(LATEST_TEST if args.test_mode else LATEST)
        batch=predict_batch(archive,model,{'store':store,'test_mode':args.test_mode})
        result={'batch':batch.model_dump(mode='json')}
        if args.publish:result['publication']=publish_batch(batch,store,generation,test_mode=args.test_mode).__dict__
        return result
    if args.command in ('validate-batch','explain'):
        from .publish import validate_batch,consumer_ages
        from .splits import features_from_bytes
        from .artifacts import load_bundle
        raw,_=store.read(args.batch_key)
        if raw is None:raise ValueError('missing prediction batch')
        batch=PredictionBatch.model_validate_json(raw);validate_batch(batch,store,test_mode=args.test_mode)
        if args.command=='validate-batch':return {'valid':True,'batch_id':batch.batch_id,**consumer_ages(batch)}
        from .explain import explain
        from .predict import approved_scopes
        from .support import assess_support
        features=features_from_bytes(checked_read(store,batch.features));bundle=load_bundle(batch.model,store,test_mode=args.test_mode)
        lookup={k.id:i for i,k in enumerate(features.keys)}
        if args.row_id not in lookup:raise ValueError('unknown row ID')
        i=lookup[args.row_id];support=assess_support(features,bundle.support,approved_scopes(bundle),batch.cutoff)
        return explain(bundle,FeatureRow(key=features.keys[i],names=features.names,values=tuple(features.values[i]),
            region=support['region_ids'][i],provenance=features.provenance,status=support['reasons'][i]))
    if args.command=='rollback':
        from .publish import rollback
        return {'rolled_back':rollback(store,args.expected_generation,test_mode=args.test_mode)}
    if args.command=='inspect':
        raw,_=store.read(args.key)
        if raw is None:raise ValueError('missing artifact')
        return {'key':args.key,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'json':json.loads(raw) if args.key.endswith('.json') else None}
    raise ValueError('unknown command')


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if not 0<args.budget_gb<=10:raise ValueError('local storage budget must be positive and at most 10 GB')
        root=Path(args.budget_root).resolve()
        if not root.is_dir():raise ValueError('existing budget root required')
        used=tree_bytes(root)
        if used>args.budget_gb*1e9 and args.command not in ('readiness','inspect'):
            raise ValueError('existing local model files exceed storage budget; inspect or use a reviewed cleanup')
        store=BudgetStore(open_store(args.store),root if not args.store.startswith('gs://') else None,int(args.budget_gb*1e9))
        # Keep temporary COG staging inside the measured local model directory.
        scratch=root/'.cache/outage-staging';scratch.mkdir(parents=True,exist_ok=True)
        old_temp=tempfile.tempdir;tempfile.tempdir=str(scratch)
        try:result=run(args,store)
        finally:tempfile.tempdir=old_temp
        print(json.dumps(result,sort_keys=True,allow_nan=False));return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print(json.dumps({'error':type(exc).__name__,'message':str(exc)}),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
