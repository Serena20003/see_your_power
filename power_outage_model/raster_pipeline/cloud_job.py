"""Cloud Run wrapper: restore pinned config/geometry, poll, execute and collect."""
import argparse
from datetime import datetime,timezone,timedelta
import json
import logging
from pathlib import Path
import tempfile
from urllib.parse import urlparse,unquote
import re
from .contracts import canonical,digest,SourceField
from .config import load_config
from .storage import GCSStore
from .catalog import list_candidates,collect_catalog
from .run import run,event,_static
from .retention import collect_garbage
from .source import ceil_hour


def candidate_urls(urls,now,lookback_hours=5):
    """Bound downloads using candidate names; GRIB remains metadata authority.

    Reject sparse late leads rather than interpolating them into hourly data.
    Selected GRIB files are still decoded/validated by collect_catalog and run.
    """
    records=[]
    for url in urls:
        match=re.search(r'/p/([^/]+)/r/([^/]+)/s/PT(\d+)H00M.grib2$',unquote(url))
        if match:
            run=datetime.fromisoformat(match[2]).replace(tzinfo=timezone.utc)
            records.append((url,match[1],run,int(match[3])))
    start=ceil_hour(now);hour=timedelta(hours=1)
    runs=sorted({r[2] for r in records if r[2]<=now},reverse=True)
    for run in runs:
        lead=int((start-run)/hour)
        fields={name:{r[3] for r in records if r[1]==name and r[2]==run}
            for name in ('TOT_PREC','T_2M','U_10M','V_10M')}
        if not all(set(range(lead+(name!='TOT_PREC'),lead+73))<=steps for name,steps in fields.items()):continue
        earliest=start-lookback_hours*hour
        selected=[r[0] for r in records if r[2]==run and 0<=r[3]<=max(78,lead+73)]
        previous=next((r for r in runs if r<run),None)
        if previous and earliest<run:
            selected.extend(r[0] for r in records if r[2]==previous and earliest<=previous+r[3]*hour<=run)
        return sorted(set(selected)),run.isoformat()
    return [],None



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
    p.add_argument('--map-bucket',help='Public bucket containing only derived map images and metadata')
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
            restore(args.config_uri,config_path)
            cfg=load_config(config_path).model_copy(update={'native_grid':str(geometry)})
            # Restore observed metadata/raw cache across ephemeral invocations.
            # Reuse the catalog's original first_seen instead of inventing backdated availability.
            restore_catalog_observations(store,root/'raw')
            routes=[{'adapter':'v1','root':'https://opendata.dwd.de/weather/nwp/v1/m/icon/p/'}]
            now=datetime.now(timezone.utc)
            urls,source_run=candidate_urls(list_candidates(routes),now,cfg.lookback_hours)
            if not urls:
                event('awaiting_hourly_source',note='No candidate run contains the next 72 actual hourly intervals; keeping last good batch')
                return
            current,_=store.read('raster/v1/latest.json')
            if current:
                pointer=json.loads(current)
                if pointer.get('target_start')==ceil_hour(now).isoformat() and pointer.get('selected_run')==source_run:
                    if args.map_bucket:
                        from .map_export import publish_weather_map
                        raw,_=store.read(pointer['manifest_key'])
                        publish_weather_map(json.loads(raw),store,GCSStore(args.map_bucket,client,cache_control='public, max-age=31536000, immutable'))
                    event('already_current',batch_id=pointer['batch_id'])
                    return
            # Fail unusable pinned inputs before downloading gigabytes of raw
            # weather and native geometry into the job's memory-backed disk.
            _static(cfg,store,False)
            restore(args.native_grid_uri,geometry)
            records=collect_catalog(urls,root/'raw')
            persist_catalog_observations(records,store)
            cutoff=datetime.now(timezone.utc);cfg=cfg.model_copy(update={'catalog':records})
            orchestrated=True
            manifest=run(cfg,cutoff,store,work_dir=root/'work')
            orchestrated=False
            if args.map_bucket:
                from .map_export import publish_weather_map
                publish_weather_map(manifest,store,GCSStore(args.map_bucket,client,cache_control='public, max-age=31536000, immutable'))
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
