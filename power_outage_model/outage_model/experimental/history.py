"""Stream authentic EAGLE-I observations; omitted/blank observations stay unknown."""
from collections import defaultdict,Counter
from datetime import date
import csv
import hashlib
import io
import json
import zipfile
import sys,time
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import shape
from raster_pipeline.contracts import canonical,digest
from ..storage import put_bytes,checked_read
from .contracts import CountyDay,ROOT,PREFIX,check_headroom

ARTICLE_URL='https://api.figshare.com/v2/articles/24237376'
COUNTIES_URL='https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_county_20m.zip'
CONUS_EXCLUDED={'02','15','60','66','69','72','78'}


def aggregate_rows(rows):
    values={};audit=Counter()
    for row in rows:
        audit['source_rows']+=1
        fips=row.get('fips_code','').strip().zfill(5);stamp=row.get('run_start_time','').strip();raw=row.get('customers_out',row.get('sum','')).strip()
        if not raw or raw.lower() in ('nan','na','null'):
            audit['missing_count_rows']+=1;continue
        try:
            count=float(raw)
            if count<0 or count!=count or count==float('inf'):raise ValueError()
            day=date.fromisoformat(stamp[:10])
            if len(fips)!=5 or not fips.isdigit():raise ValueError()
        except (ValueError,TypeError):audit['invalid_rows']+=1;continue
        key=(fips,day);previous=values.get(key)
        if previous is None:values[key]=[count,1]
        else:previous[0]=max(previous[0],count);previous[1]+=1
        if count==0:audit['explicit_zero_rows']+=1
        else:audit['positive_count_rows']+=1
    return [CountyDay(county_fips=f,day=d,reported_peak_customers=v[0],received_records=v[1]) for (f,d),v in sorted(values.items())],dict(audit)


def prepare_history(store,*,year=2018,root=ROOT,client=None):
    """No national CSV copy is written; checksum the full streaming transfer."""
    if year<2014 or year>2025:raise ValueError('unsupported published history year')
    cache_key=f'{PREFIX}/history/{year}-reference.json'
    raw,_=store.read(cache_key)
    if raw:
        ref=json.loads(raw);load_days(store,ref);return ref
    check_headroom(root,100_000_000)
    own=client is None;client=client or httpx.Client(timeout=120,follow_redirects=True)
    try:
        r=client.get(ARTICLE_URL);r.raise_for_status();article=r.json()
        matches=[f for f in article['files'] if f['name']==f'eaglei_outages_{year}.csv']
        if len(matches)!=1:raise ValueError('published history file not uniquely identified')
        file=matches[0];state={'bytes':0};md5=hashlib.md5();sha=hashlib.sha256()
        def lines():
            carry=b'';chunk_size=8*1024*1024
            for offset in range(0,file['size'],chunk_size):
                end=min(file['size']-1,offset+chunk_size-1);expected=end-offset+1;block=None
                for attempt in range(3):
                    try:
                        with client.stream('GET',file['download_url']+f'?experimental_range_v1={offset}',headers={'Range':f'bytes={offset}-{end}'}) as response:
                            response.raise_for_status()
                            if response.status_code!=206 or response.headers.get('content-range')!=f'bytes {offset}-{end}/{file["size"]}':
                                raise ValueError('exact bounded source byte range required')
                            parts=[];received=0
                            for part in response.iter_bytes(chunk_size=1024*1024):
                                received+=len(part)
                                if received>expected:raise ValueError('range exceeded declared transfer size')
                                parts.append(part)
                            block=b''.join(parts)
                            if len(block)!=expected:raise ValueError('truncated source byte range')
                        break
                    except (httpx.HTTPError,ValueError):
                        if attempt==2:raise
                        time.sleep(.5*(attempt+1))
                state['bytes']+=len(block);md5.update(block);sha.update(block)
                if offset%(64*1024*1024)==0:print(f'history stream verified {state["bytes"]:,}/{file["size"]:,} bytes',file=sys.stderr,flush=True)
                pieces=(carry+block).split(b'\n');carry=pieces.pop()
                for line in pieces:yield line.decode('utf-8-sig')+'\n'
            if carry:yield carry.decode('utf-8-sig')
        reader=csv.DictReader(lines())
        if not {'fips_code','run_start_time'}.issubset(reader.fieldnames or []) or not ({'customers_out','sum'}&set(reader.fieldnames or [])):
            raise ValueError('unsupported real county-history schema')
        days,audit=aggregate_rows(reader)
        if state['bytes']!=file['size'] or md5.hexdigest()!=file['computed_md5']:raise ValueError('incomplete or corrupt history transfer')
        if not days:raise ValueError('no usable historical observations; no fixture fallback')
        table=pa.table({'county_fips':[d.county_fips for d in days],'day':[d.day.isoformat() for d in days],
            'reported_peak_customers':[d.reported_peak_customers for d in days],'received_records':[d.received_records for d in days]})
        out=io.BytesIO();pq.write_table(table,out,compression='zstd')
        data_ref=put_bytes(store,f'{PREFIX}/history',out.getvalue(),'parquet')
        coverage=next(f for f in article['files'] if f['name']=='coverage_history.csv')
        rr=client.get(coverage['download_url']+'?experimental_full=v1');rr.raise_for_status()
        if hashlib.md5(rr.content).hexdigest()!=coverage['computed_md5']:raise ValueError('coverage metadata checksum mismatch')
        cov_ref=put_bytes(store,f'{PREFIX}/sources',rr.content,'csv')
        provenance={'schema_version':'experimental-county-history/v1','fixture':False,'year':year,'observations':data_ref,'audit':audit,
            'source':{'article_url':ARTICLE_URL,'doi':article['doi'],'title':article['title'],'license':article.get('license'),
                'authors':[{k:a.get(k) for k in ('full_name','id')} for a in article.get('authors',[])],
                'file':file,'verified_bytes':state['bytes'],'verified_md5':md5.hexdigest(),'sha256':sha.hexdigest()},
            'county_days':len(days),'county_count':len({d.county_fips for d in days}),'coverage_metadata':cov_ref,
            'missing_policy':'omitted rows and blank counts are unknown; never zero-filled',
            'daily_target':'maximum reported customers among available valid observations in UTC day; not guaranteed full-day maximum',
            'forecast_or_incident_archive':False}
        ref=put_bytes(store,f'{PREFIX}/history',canonical(provenance),'json')
        store.put(cache_key,canonical(ref));return ref
    finally:
        if own:client.close()


def load_days(store,ref):
    meta=json.loads(checked_read(store,ref))
    if meta.get('schema_version')!='experimental-county-history/v1':raise ValueError('incompatible history manifest')
    t=pq.read_table(io.BytesIO(checked_read(store,meta['observations'])))
    rows=t.to_pylist()
    return [CountyDay(county_fips=r['county_fips'],day=date.fromisoformat(r['day']),reported_peak_customers=r['reported_peak_customers'],received_records=r['received_records']) for r in rows],meta


def prepare_geometry(store,*,root=ROOT):
    """Generalized polygon support, not a claim of exact land-only boundaries."""
    cache=f'{PREFIX}/geometry/reference-v2.json';raw,_=store.read(cache)
    if raw:
        ref=json.loads(raw);load_geometry(store,ref);return ref
    check_headroom(root,50_000_000)
    import shapefile
    with httpx.Client(timeout=60,follow_redirects=True) as client:
        r=client.get(COUNTIES_URL+'?experimental_full=v1');r.raise_for_status();raw=r.content
    if len(raw)>30_000_000:raise ValueError('county geometry exceeds reviewed size')
    archive=zipfile.ZipFile(io.BytesIO(raw));names=archive.namelist()
    def member(suffix):return io.BytesIO(archive.read(next(n for n in names if n.endswith(suffix))))
    from pyproj import CRS,Transformer
    from shapely.ops import transform
    source_crs=CRS.from_wkt(member('.prj').getvalue().decode())
    to_wgs84=Transformer.from_crs(source_crs,4326,always_xy=True).transform
    reader=shapefile.Reader(shp=member('.shp'),shx=member('.shx'),dbf=member('.dbf'))
    features=[]
    for item in reader.iterShapeRecords():
        rec=item.record.as_dict();fips=rec['GEOID']
        if fips[:2] in CONUS_EXCLUDED:continue
        polygon=transform(to_wgs84,shape(item.shape.__geo_interface__))
        if polygon.is_empty or not polygon.is_valid:polygon=polygon.buffer(0)
        if polygon.is_empty or not polygon.is_valid:continue
        features.append({'type':'Feature','properties':{'GEOID':fips,'NAME':rec.get('NAME'),'STATEFP':rec['STATEFP']},'geometry':polygon.__geo_interface__})
    record={'type':'FeatureCollection','features':features,'metadata':{'source_url':COUNTIES_URL,'source_sha256':hashlib.sha256(raw).hexdigest(),
        'source_bytes':len(raw),'source_crs':source_crs.to_string(),'normalized_crs':'EPSG:4326','crs_transform':'pyproj always_xy source to EPSG:4326',
        'vintage':'2025; retrospective boundary approximation for 2018 history',
        'support':'generalized_county_polygon_including_unclipped_inland_water','fixture':False}}
    ref=put_bytes(store,f'{PREFIX}/geometry',canonical(record),'geojson');store.put(cache,canonical(ref));return ref


def load_geometry(store,ref):
    record=json.loads(checked_read(store,ref))
    return {f['properties']['GEOID']:shape(f['geometry']) for f in record['features']},record['metadata']
