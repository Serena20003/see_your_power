import bz2
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import time
import httpx
from .contracts import InputInventory, digest


def file_sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()


def download_inputs(selection, cache_dir, *, concurrency=4, retries=3, timeout=90, inspect=None):
    from .decode import inspect_grib, verify_metadata
    inspect=inspect or inspect_grib
    root=Path(cache_dir); root.mkdir(parents=True,exist_ok=True)
    def fetch(f):
        if f.path and f.sha256 and Path(f.path).exists() and file_sha(f.path)==f.sha256:
            verify_metadata(f,inspect(f.path))
            return f,0
        key=digest({'url':f.url,'run':f.run.isoformat(),'step':f.end_step})
        dest=root/(key+'.grib2'); side=root/(key+'.json')
        import json
        if dest.exists() and side.exists():
            saved=json.loads(side.read_text()); checksum=file_sha(dest)
            if checksum==saved['sha256'] and (not f.sha256 or checksum==f.sha256):
                verify_metadata(f,inspect(dest))
                return f.model_copy(update={'path':str(dest),'sha256':checksum,
                    'retrieved_at':datetime.fromisoformat(saved['retrieved_at'])}),0
        for attempt in range(retries+1):
            tmp=None
            try:
                with tempfile.NamedTemporaryFile(dir=root,delete=False) as out:
                    tmp=Path(out.name)
                    decompressor=bz2.BZ2Decompressor() if f.url.endswith('.bz2') else None
                    count=0
                    with httpx.stream('GET',f.url,timeout=timeout,follow_redirects=True) as response:
                        response.raise_for_status()
                        for chunk in response.iter_bytes(1024*1024):
                            count+=len(chunk)
                            out.write(decompressor.decompress(chunk) if decompressor else chunk)
                    if decompressor and not decompressor.eof: raise ValueError('truncated bzip2 input')
                    out.flush(); os.fsync(out.fileno())
                checksum=file_sha(tmp)
                if f.sha256 and checksum!=f.sha256: raise ValueError('source checksum mismatch')
                verify_metadata(f,inspect(tmp))
                os.replace(tmp,dest)
                now=datetime.now(timezone.utc)
                # A unique temp sidecar avoids partial cache metadata on interruption.
                with tempfile.NamedTemporaryFile(mode='w',dir=root,delete=False) as meta:
                    json.dump({'sha256':checksum,'retrieved_at':now.isoformat()},meta)
                    meta_path=meta.name
                os.replace(meta_path,side)
                return f.model_copy(update={'path':str(dest),'sha256':checksum,'retrieved_at':now}),count
            except Exception:
                if tmp: tmp.unlink(missing_ok=True)
                if attempt==retries: raise
                time.sleep(min(2**attempt,8))
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results=list(executor.map(fetch,selection.fields))
    from .decode import tool_versions
    return InputInventory(selection=selection,fields=[r[0] for r in results],
        downloaded_bytes=sum(r[1] for r in results),tool_versions=tool_versions())
