"""Cloud Run wrapper: restore pinned config/geometry, poll, execute and collect."""
import argparse
from datetime import datetime,timezone
import json
import logging
from pathlib import Path
import tempfile
from urllib.parse import urlparse
from .contracts import canonical,digest,SourceField
from .config import load_config
from .storage import GCSStore
from .catalog import list_candidates,collect_catalog
from .run import run,event
from .retention import collect_garbage



def restore_catalog_observations(store,cache_dir):
    """Restore small first-seen/checksum records, never archived raw weather."""
    raw,_=store.read('raster/v1/source-catalog.json')
    if raw is None:return
    root=Path(cache_dir);root.mkdir(parents=True,exist_ok=True)
    for record in json.loads(raw):
        # Accept metadata from older catalogs without fetching their raw_key.
        data={k:v for k,v in record.items() if k not in ('raw_key','cache_name')}
        field=SourceField.model_validate(data)
        key=digest({'url':field.url});path=root/(key+'.grib2')
        field=field.model_copy(update={'path':str(path)})
        (root/(key+'.json')).write_bytes(canonical(field))


def persist_catalog_observations(records,store):
    _,generation=store.read('raster/v1/source-catalog.json')
    store.replace_pointer('raster/v1/source-catalog.json',canonical([
        field.model_dump(mode='json') for field in records]),generation)


def main():
    p=argparse.ArgumentParser();p.add_argument('--config-uri',required=True);p.add_argument('--native-grid-uri',required=True);p.add_argument('--bucket',required=True)
    args=p.parse_args();logging.basicConfig(level=logging.INFO,format='%(message)s')
    from google.cloud import storage
    client=storage.Client();store=GCSStore(args.bucket,client)
    def restore(uri,path):
        u=urlparse(uri)
        if u.scheme!='gs':raise ValueError('cloud config and geometry must be pinned gs:// objects')
        client.bucket(u.netloc).blob(u.path.lstrip('/')).download_to_filename(path)
    orchestrated=False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config_path=root/'config.json';geometry=root/'grid.nc'
            restore(args.config_uri,config_path);restore(args.native_grid_uri,geometry)
            cfg=load_config(config_path).model_copy(update={'native_grid':str(geometry)})
            # Restore observed metadata/raw cache across ephemeral invocations.
            # Reuse the catalog's original first_seen instead of inventing backdated availability.
            restore_catalog_observations(store,root/'raw')
            routes=[{'adapter':'v1','root':'https://opendata.dwd.de/weather/nwp/v1/m/icon/p/'}]
            records=collect_catalog(list_candidates(routes),root/'raw')
            persist_catalog_observations(records,store)
            cutoff=datetime.now(timezone.utc);cfg=cfg.model_copy(update={'catalog':records})
            orchestrated=True
            manifest=run(cfg,cutoff,store,work_dir=root/'work')
            orchestrated=False
            # Emit only once per new actual source run, not every 30m poll.
            heartbeat='raster/v1/eligible-run.json';old,generation=store.read(heartbeat)
            if old is None or json.loads(old)['selected_run']!=manifest['selected_run']:
                if store.replace_pointer(heartbeat,canonical({'selected_run':manifest['selected_run'],'cutoff':manifest['cutoff']}),generation):
                    event('eligible_complete_run',selected_run=manifest['selected_run'],cutoff=manifest['cutoff'])
            collect_garbage(store,dry_run=False)
    except Exception as exc:
        if not orchestrated:event('cloud_job_failed',failure_reason=f'{type(exc).__name__}: {exc}')
        raise


if __name__=='__main__':main()
