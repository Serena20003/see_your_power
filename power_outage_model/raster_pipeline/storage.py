"""Immutable objects and compare-and-swap publication pointers."""
from contextlib import contextmanager
import hashlib
import mimetypes
import os
from pathlib import Path,PurePosixPath
import tempfile


def check_key(key):
    p=PurePosixPath(key)
    if not key or p.is_absolute() or '..' in p.parts or '\\' in key:
        raise ValueError('unsafe object key')
    return p


class LocalStore:
    def __init__(self,root):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)

    def path(self,key):
        check_key(key);path=self.root/key
        if not path.resolve().is_relative_to(self.root):raise ValueError('object path escapes store')
        return path

    @contextmanager
    def lock(self):
        import fcntl
        with (self.root/'.publication.lock').open('a+b') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(handle,fcntl.LOCK_UN)

    def read(self,key):
        try:data=self.path(key).read_bytes()
        except FileNotFoundError:return None,None
        return data,hashlib.sha256(data).hexdigest()

    def _write(self,key,data):
        path=self.path(key);path.parent.mkdir(parents=True,exist_ok=True)
        tmp=None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as out:
                tmp=Path(out.name);out.write(data);out.flush();os.fsync(out.fileno())
            os.replace(tmp,path)
            fd=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        finally:
            if tmp:tmp.unlink(missing_ok=True)

    def put(self,key,data):
        with self.lock():
            previous,_=self.read(key)
            if previous is not None:
                if previous!=data:raise ValueError(f'immutable object mismatch: {key}')
                return
            self._write(key,data)

    def replace_pointer(self,key,data,expected_generation):
        with self.lock():
            _,generation=self.read(key)
            if generation!=expected_generation:return False
            self._write(key,data);return True

    @contextmanager
    def reference_lock(self):
        import fcntl
        with (self.root/'.references.lock').open('a+b') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(handle,fcntl.LOCK_UN)

    def modified(self,key):
        from datetime import datetime,timezone
        return datetime.fromtimestamp(self.path(key).stat().st_mtime,timezone.utc)

    def keys(self,prefix=''):
        check_key(prefix or '.')
        return sorted(str(p.relative_to(self.root)) for p in (self.root/prefix).rglob('*') if p.is_file() and not p.name.startswith('.'))

    def delete(self,key):self.path(key).unlink(missing_ok=True)


class GCSStore:
    def __init__(self,bucket,client=None,cache_control=None):
        if client is None:
            from google.cloud import storage
            client=storage.Client()
        self.bucket=client.bucket(bucket)
        self.cache_control=cache_control

    def _upload_blob(self,key):
        blob=self.bucket.blob(key)
        if key.endswith('/latest.json'):blob.cache_control='no-store'
        elif self.cache_control:blob.cache_control=self.cache_control
        return blob

    def read(self,key):
        from google.api_core.exceptions import NotFound,PreconditionFailed
        check_key(key)
        # Read exactly the generation whose token is returned.
        for _ in range(4):
            blob=self.bucket.blob(key)
            try:
                blob.reload();generation=int(blob.generation)
                return blob.download_as_bytes(if_generation_match=generation),str(generation)
            except NotFound:return None,None
            except PreconditionFailed:continue
        raise RuntimeError('object repeatedly changed during generation-consistent read')

    def put(self,key,data):
        from google.api_core.exceptions import PreconditionFailed
        check_key(key)
        try:self._upload_blob(key).upload_from_string(data,if_generation_match=0,checksum='crc32c',content_type=mimetypes.guess_type(key)[0] or 'application/octet-stream')
        except PreconditionFailed:
            old,_=self.read(key)
            if old!=data:raise ValueError(f'immutable object mismatch: {key}')

    def replace_pointer(self,key,data,expected_generation):
        from google.api_core.exceptions import PreconditionFailed
        check_key(key)
        try:
            self._upload_blob(key).upload_from_string(data,if_generation_match=int(expected_generation or 0),checksum='crc32c',content_type=mimetypes.guess_type(key)[0] or 'application/octet-stream')
            return True
        except PreconditionFailed:return False

    @contextmanager
    def reference_lock(self):
        from google.api_core.exceptions import PreconditionFailed
        import uuid
        key='raster/v1/.references.lock';blob=self.bucket.blob(key)
        try:blob.upload_from_string(uuid.uuid4().hex,if_generation_match=0)
        except PreconditionFailed:raise RuntimeError('publication/retention lock held; a crashed job requires operator inspection before removing the lock')
        generation=int(blob.generation)
        try:yield
        finally:blob.delete(if_generation_match=generation)

    def modified(self,key):
        blob=self.bucket.blob(key);blob.reload();return blob.time_created

    def keys(self,prefix=''):return [b.name for b in self.bucket.list_blobs(prefix=prefix)]
    def delete(self,key):
        check_key(key);self.bucket.blob(key).delete()


def open_store(output):
    if output.startswith('gs://'):
        bucket=output[5:].rstrip('/')
        if '/' in bucket:raise ValueError('GCS output must be a bucket root')
        return GCSStore(bucket)
    return LocalStore(output)
