import argparse
from datetime import datetime,timezone
import json
import logging
from pathlib import Path
from .contracts import GridSpec,canonical
from .config import load_config
from .storage import open_store
from .run import run,recalculate,read_json
from .risk import load_risk
from .static import build_static


def parser():
    p=argparse.ArgumentParser(description='Hourly ICON weather and exploratory environmental risk COG pipeline')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('run','poll-run'):
        r=sub.add_parser(name);r.add_argument('--config',required=True);r.add_argument('--output',required=True)
        r.add_argument('--cutoff',required=name=='run');r.add_argument('--work-dir',default='.cache')
        r.add_argument('--fixture-dir');r.add_argument('--dry-run',action='store_true')
        if name=='poll-run':r.add_argument('--routes',required=True)
    s=sub.add_parser('build-static');s.add_argument('--config',required=True);s.add_argument('--output',required=True);s.add_argument('--grid-config')
    s=sub.add_parser('recalculate');s.add_argument('--weather-manifest',required=True);s.add_argument('--static-manifest');s.add_argument('--risk-config',required=True);s.add_argument('--output',required=True)
    s.add_argument('--allow-static-gaps',action='store_true',default=None)
    s=sub.add_parser('catalog');s.add_argument('--routes',required=True);s.add_argument('--cache-dir',required=True);s.add_argument('--output',required=True)
    s=sub.add_parser('gc');s.add_argument('--output',required=True);s.add_argument('--apply',action='store_true')
    s=sub.add_parser('make-fixture');s.add_argument('--output',required=True);s.add_argument('--cutoff',default='2026-11-16T06:00:00+00:00')
    return p


def main(argv=None):
    args=parser().parse_args(argv);logging.basicConfig(level=logging.INFO,format='%(message)s')
    if args.command=='make-fixture':
        from .fixtures import generate_fixture
        grid=GridSpec(grid_id='fixture-two-cells-v1',west=-120,south=34,east=-118,north=35,resolution=1)
        generate_fixture(args.output,datetime.fromisoformat(args.cutoff),grid)
        print(json.dumps({'fixture_dir':args.output,'note':'explicit synthetic test data; not a live batch'}));return
    if args.command in ('catalog','poll-run'):
        from .catalog import list_candidates,collect_catalog
        routes=json.loads(Path(args.routes).read_text())
        cache=args.cache_dir if args.command=='catalog' else str(Path(args.work_dir)/'raw')
        records=collect_catalog(list_candidates(routes),cache)
        if args.command=='catalog':Path(args.output).write_bytes(canonical(records));return
    store=open_store(args.output)
    if args.command=='gc':
        from .retention import collect_garbage
        print(json.dumps({'dry_run':not args.apply,'objects':collect_garbage(store,dry_run=not args.apply)}));return
    if args.command=='build-static':
        config=json.loads(Path(args.config).read_text())
        grid=load_config(args.grid_config).grid if args.grid_config else GridSpec()
        m=build_static(config,grid,store);print(canonical(m).decode());return
    if args.command=='recalculate':
        m=read_json(store,args.weather_manifest)
        result=recalculate(m,store,load_risk(args.risk_config),static_manifest=args.static_manifest,allow_static_gaps=args.allow_static_gaps)
    else:
        cfg=load_config(args.config)
        if args.command=='poll-run':cfg=cfg.model_copy(update={'catalog':records})
        if args.fixture_dir:
            from .fixtures import load_fixture_config
            from .run import fixture_static
            fixture_cfg=load_fixture_config(args.fixture_dir)
            static=fixture_static(fixture_cfg.grid,store)
            cfg=fixture_cfg.model_copy(update={'static_manifest':f'raster/v1/static/{static.static_version}/manifest.json',
                'risk_config':cfg.risk_config})
        cutoff=datetime.fromisoformat(args.cutoff.replace('Z','+00:00')) if args.cutoff else datetime.now(timezone.utc)
        result=run(cfg,cutoff,store,work_dir=args.work_dir,fixture_dir=args.fixture_dir,dry_run=args.dry_run)
    print(json.dumps({'status':result.get('status','complete'),'batch_id':result.get('batch_id')}))


if __name__=='__main__':main()
