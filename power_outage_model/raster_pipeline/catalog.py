"""Observed DWD directory adapters. Filenames are candidates, GRIB is authority."""
from datetime import datetime,timezone
from html.parser import HTMLParser
import bz2
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urljoin,urlparse,unquote
from concurrent.futures import ThreadPoolExecutor
import httpx
from .contracts import FIELDS,SourceField,canonical,digest
from .decode import inspect_grib
from .source import validate_field
from .download import file_sha


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            href=dict(attrs).get('href')
            if href and not href.startswith(('../','/','?','#')):self.links.append(href)


def directory(url,client):
    response=client.get(url);response.raise_for_status();parser=Links();parser.feed(response.text)
    return [urljoin(url,href) for href in parser.links if urlparse(urljoin(url,href)).netloc==urlparse(url).netloc]


def list_candidates(routes,*,max_lead=102,client=None):
    """routes: {adapter: legacy|v1, root: URL}, configurable per source release."""
    own=client is None;client=client or httpx.Client(timeout=90,follow_redirects=True)
    urls=set()
    try:
        for route in routes:
            root=route['root'].rstrip('/')+'/'
            if route['adapter']=='legacy':
                for cycle in ('00','06','12','18'):
                    for field in FIELDS:
                        for url in directory(f'{root}{cycle}/{field.lower()}/',client):
                            match=re.search(r'_(\d{3})_[A-Z0-9_]+\.grib2\.bz2$',url)
                            if match and int(match[1])<=max_lead:urls.add(url)
            elif route['adapter']=='v1':
                for field in FIELDS:
                    for run in directory(f'{root}{field}/r/',client):
                        if not re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/$',unquote(run)):continue
                        for url in directory(run+'s/',client):
                            match=re.search(r'PT(\d+)H00M\.grib2$',url)
                            if match and int(match[1])<=max_lead:urls.add(url)
            else:raise ValueError('unsupported DWD directory adapter')
        if not urls:raise ValueError('DWD layout changed: no supported GRIB candidates found')
        return sorted(urls)
    finally:
        if own:client.close()


def collect_catalog(urls,cache_dir,*,concurrency=4,retries=3,timeout=90):
    root=Path(cache_dir);root.mkdir(parents=True,exist_ok=True)
    def collect(url):
        # URL cache is immutable for this observed run/step. Verify every reuse.
        cachekey=digest({'url':url});path=root/(cachekey+'.grib2');side=root/(cachekey+'.json')
        previous=SourceField.model_validate_json(side.read_bytes()) if side.exists() else None
        if path.exists() and previous is not None:
            record=previous.model_copy(update={'path':str(path)})
            if file_sha(path)==record.sha256:
                actual=inspect_grib(path)
                from .decode import verify_metadata
                verify_metadata(record,actual);return record
        for attempt in range(retries+1):
            tmp=None
            try:
                with tempfile.NamedTemporaryFile(dir=root,delete=False) as out:
                    tmp=Path(out.name);decoder=bz2.BZ2Decompressor() if url.endswith('.bz2') else None
                    with httpx.stream('GET',url,timeout=timeout,follow_redirects=True) as response:
                        response.raise_for_status()
                        for block in response.iter_bytes(1024*1024):out.write(decoder.decompress(block) if decoder else block)
                    if decoder and not decoder.eof:raise ValueError('truncated compressed GRIB')
                    out.flush();os.fsync(out.fileno())
                meta=inspect_grib(tmp)
                record=SourceField(**meta,url=url,first_seen=datetime.now(timezone.utc),
                    retrieved_at=datetime.now(timezone.utc),sha256=file_sha(tmp),path=str(path))
                if previous is not None and previous.url==url and previous.sha256==record.sha256:
                    record=record.model_copy(update={'first_seen':previous.first_seen})
                validate_field(record)
                # Check candidate names against metadata instead of trusting them.
                match=re.search(r'_(\d{10})_(\d{3})_([A-Z0-9_]+)\.grib2',url)
                if match and (record.run.strftime('%Y%m%d%H')!=match[1] or record.end_step!=int(match[2]) or record.field!=match[3]):raise ValueError('legacy filename/GRIB conflict')
                if '/p/' in url:
                    match=re.search(r'/p/([^/]+)/r/([^/]+)/s/PT(\d+)H00M.grib2$',unquote(url))
                    if not match or record.field!=match[1] or record.run.strftime('%Y-%m-%dT%H:%M')!=match[2] or record.end_step!=int(match[3]):raise ValueError('v1 filename/GRIB conflict')
                os.replace(tmp,path)
                with tempfile.NamedTemporaryFile(dir=root,delete=False) as out:
                    out.write(canonical(record));side_tmp=out.name
                os.replace(side_tmp,side)
                return record
            except Exception:
                if tmp:tmp.unlink(missing_ok=True)
                if attempt==retries:raise
                time.sleep(min(2**attempt,8))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:records=list(pool.map(collect,urls))
    return records
