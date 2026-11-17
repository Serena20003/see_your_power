"""Budgeted object writes. Archives are content-addressed; no automatic eviction."""
from pathlib import Path
from contextlib import contextmanager
import fcntl
import hashlib
from raster_pipeline.storage import LocalStore, GCSStore, open_store
from raster_pipeline.contracts import canonical

DEFAULT_TARGET = 5_000_000_000
HARD_LIMIT = 10_000_000_000


def tree_bytes(root):
    return sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file() and not p.is_symlink())


class BudgetStore:
    def __init__(self, store, budget_root=None, limit=DEFAULT_TARGET):
        if not 0 < limit <= HARD_LIMIT: raise ValueError('budget must be positive and at most 10 GB')
        if isinstance(store,LocalStore) and budget_root is None:
            raise ValueError('local storage requires a budget root covering all related files')
        self.store=store; self.root=Path(budget_root).resolve() if budget_root else None
        if self.root and not store.root.is_relative_to(self.root):
            raise ValueError('output store must be inside budget root')
        self.limit=limit
    def read(self,key): return self.store.read(key)
    def keys(self,prefix=''): return self.store.keys(prefix)
    @contextmanager
    def _budget_lock(self):
        if self.root is None:
            yield
            return
        with (self.root/'.outage-budget.lock').open('a+b') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(handle,fcntl.LOCK_UN)
    def _check(self,key,data,*,replace=False):
        old,_=self.read(key)
        if self.root and (old is None or replace):
            used=tree_bytes(self.root)
            if used+2*len(data)>self.limit:
                raise ValueError(f'local storage budget exceeded ({used} used; {self.limit} limit); use remote archive or free reviewed caches')
    def put(self,key,data):
        with self._budget_lock():
            self._check(key,data)
            return self.store.put(key,data)
    def replace_pointer(self,key,data,expected_generation):
        with self._budget_lock():
            self._check(key,data,replace=True)
            return self.store.replace_pointer(key,data,expected_generation)


def checked_read(store,ref):
    raw,_=store.read(ref['key'])
    if raw is None or hashlib.sha256(raw).hexdigest()!=ref['sha256']:
        raise ValueError(f"missing object or checksum mismatch: {ref['key']}")
    return raw


def put_bytes(store,prefix,raw,suffix):
    sha=hashlib.sha256(raw).hexdigest(); key=f'{prefix}/{sha}.{suffix}'
    store.put(key,raw)
    return {'key':key,'sha256':sha,'bytes':len(raw)}
