"""One-shot experimental workflows; outputs never enter the strict release pointer."""
import argparse,json,sys
import httpx
from pathlib import Path
from raster_pipeline.contracts import canonical
from ..storage import checked_read,put_bytes,tree_bytes,BudgetStore,LocalStore
from .contracts import ROOT,PREFIX,budget_store,SamplingPolicy


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--store',default=str(ROOT/'output'),help='Local experimental object store; portable bundles contain output/')
    p.add_argument('--budget-root',default=str(ROOT),help='Root covering all related local files')
    p.add_argument('--test-mode',action='store_true',help='Fixture-only tests; never substitutes for real history')
    s=p.add_subparsers(dest='command',required=True)
    s.add_parser('inspect')
    a=s.add_parser('prepare');a.add_argument('--year',type=int,default=2018)
    a=s.add_parser('sample');a.add_argument('--inputs',required=True);a.add_argument('--points',type=int,default=8);a.add_argument('--max-parents',type=int,default=180000)
    a=s.add_parser('train');a.add_argument('--dataset-ref',required=True)
    a=s.add_parser('predict');a.add_argument('--model-ref',required=True);a.add_argument('--date',required=True);a.add_argument('--counties',required=True,help='Comma-separated supported county FIPS, or all')
    a=s.add_parser('export');a.add_argument('--model-ref',required=True)
    a=s.add_parser('validate');a.add_argument('--model-ref',required=True)
    return p


def _read(path):return json.loads(Path(path).read_text())


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        store=BudgetStore(LocalStore(args.store),args.budget_root,limit=10_000_000_000)
        if args.command=='inspect':
            result={'experimental':True,'locations_simulated':True,'local_model_directory_bytes':tree_bytes(args.budget_root),
                'hard_limit_bytes':10_000_000_000,'preferred_limit_bytes':5_000_000_000,
                'artifacts':store.keys(PREFIX),'fixture_fallback':False}
        elif args.command=='prepare':
            if args.test_mode:raise ValueError('prepare downloads real history; test mode is not a fake-data source')
            from .history import prepare_history,prepare_geometry
            result={'history_ref':prepare_history(store,year=args.year,root=Path(args.budget_root)),'geometry_ref':prepare_geometry(store,root=Path(args.budget_root))}
        elif args.command=='sample':
            from .dataset import build_dataset
            inputs=_read(args.inputs);ref=build_dataset(store,inputs['history_ref'],inputs['geometry_ref'],
                policy=SamplingPolicy(points_per_parent=args.points),max_parents=args.max_parents,root=Path(args.budget_root))
            result={'dataset_ref':ref,'experimental':True,'locations_simulated':True}
        elif args.command=='train':
            from .train import train_model
            result=train_model(store,_read(args.dataset_ref),test_mode=args.test_mode)
        elif args.command=='predict':
            from .predict import predict_counties
            from .train import load_model
            ref=_read(args.model_ref)
            if args.counties=='all':
                _,meta=load_model(store,ref,test_mode=args.test_mode);counties=meta['supported_county_fips']
            else:counties=[c.strip() for c in args.counties.split(',') if c.strip()]
            result=predict_counties(store,ref,args.date,counties,test_mode=args.test_mode)
            result={'prediction_ref':put_bytes(store,f'{PREFIX}/predictions',canonical(result),'json'),
                'model_id':result['model_id'],'county_count':len(result['records'])//3,'experimental':True,'locations_simulated':True,
                'unsupported_counties':result['unsupported_counties'],'calendar_extrapolation':result['calendar_extrapolation']}
        elif args.command=='export':
            from .portable import export_bundle
            result=export_bundle(store,_read(args.model_ref),test_mode=args.test_mode)
        elif args.command=='validate':
            from .train import load_model
            _,meta=load_model(store,_read(args.model_ref),test_mode=args.test_mode)
            evaluation=json.loads(checked_read(store,meta['evaluation_ref']))
            result={'valid':True,'model_id':meta['model_id'],'experimental':True,'locations_simulated':True,
                'physical_outage_probability':False,'feature_names':meta['features'],'fixture':meta['fixture'],
                'evaluation_metrics':evaluation['metrics']}
        else:raise ValueError('unknown command')
        print(json.dumps(result,sort_keys=True,allow_nan=False));return 0
    except (ValueError,OSError,KeyError,TypeError,httpx.HTTPError) as exc:
        print(json.dumps({'error':type(exc).__name__,'message':str(exc),'fixture_fallback':False}),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
