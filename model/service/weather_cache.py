"""
Weather data cache with GCS-backed durable storage and in-process LRU.

Architecture (per plan §2):
  - Up to 256 source entries in a bounded in-process LRU (warm reads, no GCS hit).
  - GCS `web-weather/v1/` prefix for durable shared state across revisions/processes.
  - Per-key distributed lease using GCS generation-match CAS; 60-second lease TTL.
  - Inline refresh: the winning requester fetches providers within a 25-second total
    deadline; losers receive stale prior data with refresh_state=updating.
  - No scheduler, no background task, no Cloud Tasks queue.

Each GCS object stores a JSON-serialised WeatherCacheEntry (source payload +
refresh state + lease metadata).  The in-process LRU is authoritative only while
entries are within their source TTL; after expiry, GCS is consulted before any
provider request.

Usage:
    cache = WeatherCache(bucket_name="runway-weather")
    entry = await cache.get_or_refresh(lat, lon, refresh_fn)
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WEATHER_TTL_SECONDS = 60 * 60          # 60 minutes
ALERT_TTL_SECONDS = 5 * 60             # 5 minutes
WEATHER_STALE_MAX_SECONDS = 6 * 3600   # 6 hours before scores are suppressed
ALERT_STALE_MAX_SECONDS = 15 * 60      # 15 minutes before alerts become unavailable
LEASE_TTL_SECONDS = 60
GCS_PREFIX = "web-weather/v1/"
LRU_MAX_ENTRIES = 256

# Bounded backoff: 30, 60, 120, 240, 300 seconds
_BACKOFF_SEQUENCE = [30, 60, 120, 240, 300]


def _backoff_seconds(failure_count: int) -> int:
    idx = min(failure_count - 1, len(_BACKOFF_SEQUENCE) - 1)
    return _BACKOFF_SEQUENCE[max(idx, 0)]


# ---------------------------------------------------------------------------
# Coordinate normalisation
# ---------------------------------------------------------------------------

def normalise_coord(v: float) -> float:
    """Round to 3 decimal places; convert -0.0 to 0.0."""
    rounded = round(v, 3)
    return 0.0 if rounded == 0.0 else rounded


def cache_key(lat: float, lon: float, method: str = "weather_score_v1") -> str:
    """Stable string key for a normalised coordinate + method version."""
    q_lat = normalise_coord(lat)
    q_lon = normalise_coord(lon)
    return f"{q_lat:.3f},{q_lon:.3f}|{method}"


# ---------------------------------------------------------------------------
# In-process LRU
# ---------------------------------------------------------------------------

@dataclass
class _LRUEntry:
    data: dict[str, Any]           # raw decoded JSON of CacheEntry
    inserted_at: float             # time.monotonic()


class _LRU:
    """Simple bounded LRU mapping key → _LRUEntry."""

    def __init__(self, maxsize: int = LRU_MAX_ENTRIES) -> None:
        self._maxsize = maxsize
        # Using a plain dict (insertion-ordered in Python 3.7+)
        self._cache: dict[str, _LRUEntry] = {}

    def get(self, key: str) -> Optional[_LRUEntry]:
        entry = self._cache.pop(key, None)
        if entry is not None:
            self._cache[key] = entry  # move to end (most-recently-used)
        return entry

    def put(self, key: str, data: dict[str, Any]) -> None:
        self._cache.pop(key, None)
        self._cache[key] = _LRUEntry(data=data, inserted_at=time.monotonic())
        while len(self._cache) > self._maxsize:
            # Evict least-recently-used (first item)
            self._cache.pop(next(iter(self._cache)))

    def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)


# ---------------------------------------------------------------------------
# Cache entry schema (stored in GCS as JSON)
# ---------------------------------------------------------------------------

@dataclass
class CacheEntry:
    """
    Serialisable state for one source key.
    'weather_payload' and 'alerts_payload' are raw provider responses (dicts).
    """
    key: str
    weather_payload: Optional[dict[str, Any]]
    alerts_payload: Optional[dict[str, Any]]
    weather_retrieved_at: Optional[float]   # Unix timestamp
    alerts_retrieved_at: Optional[float]
    failure_count: int = 0                  # consecutive refresh failures
    next_retry_eligible: Optional[float] = None  # Unix timestamp
    # Lease fields
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[float] = None
    # GCS generation at time of last read (used for CAS writes)
    gcs_generation: Optional[int] = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "weather_payload": self.weather_payload,
            "alerts_payload": self.alerts_payload,
            "weather_retrieved_at": self.weather_retrieved_at,
            "alerts_retrieved_at": self.alerts_retrieved_at,
            "failure_count": self.failure_count,
            "next_retry_eligible": self.next_retry_eligible,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], gcs_generation: Optional[int] = None) -> "CacheEntry":
        return cls(
            key=d["key"],
            weather_payload=d.get("weather_payload"),
            alerts_payload=d.get("alerts_payload"),
            weather_retrieved_at=d.get("weather_retrieved_at"),
            alerts_retrieved_at=d.get("alerts_retrieved_at"),
            failure_count=d.get("failure_count", 0),
            next_retry_eligible=d.get("next_retry_eligible"),
            lease_owner=d.get("lease_owner"),
            lease_expires_at=d.get("lease_expires_at"),
            gcs_generation=gcs_generation,
        )

    def weather_age(self) -> Optional[float]:
        if self.weather_retrieved_at is None:
            return None
        return time.time() - self.weather_retrieved_at

    def alerts_age(self) -> Optional[float]:
        if self.alerts_retrieved_at is None:
            return None
        return time.time() - self.alerts_retrieved_at

    def weather_due(self) -> bool:
        age = self.weather_age()
        return age is None or age >= WEATHER_TTL_SECONDS

    def alerts_due(self) -> bool:
        age = self.alerts_age()
        return age is None or age >= ALERT_TTL_SECONDS

    def retry_blocked(self) -> bool:
        if self.next_retry_eligible is None:
            return False
        return time.time() < self.next_retry_eligible

    def lease_active(self) -> bool:
        if self.lease_owner is None or self.lease_expires_at is None:
            return False
        return time.time() < self.lease_expires_at


# ---------------------------------------------------------------------------
# GCS adapter (runs sync SDK calls in a thread executor)
# ---------------------------------------------------------------------------

class _GCSAdapter:
    """
    Thin async wrapper around google-cloud-storage.
    Uses generation-match preconditions for CAS writes (distributed lease).
    Falls back gracefully when GCS is not configured.
    """

    def __init__(self, bucket_name: str) -> None:
        self._bucket_name = bucket_name
        self._client: Any = None
        self._bucket: Any = None
        self._available = False

    def _ensure_client(self) -> bool:
        if self._client is not None:
            return self._available
        try:
            from google.cloud import storage  # type: ignore
            self._client = storage.Client()
            self._bucket = self._client.bucket(self._bucket_name)
            self._available = True
            logger.info("GCS cache connected to bucket %s", self._bucket_name)
        except Exception as exc:
            logger.warning("GCS unavailable (%s); using in-memory cache only", exc)
            self._available = False
        return self._available

    def _blob_name(self, key: str) -> str:
        # Replace characters unsafe in GCS blob names
        safe = key.replace("|", "/").replace(",", "_")
        return f"{GCS_PREFIX}{safe}.json"

    def _sync_read(self, key: str) -> Optional[tuple[dict[str, Any], int]]:
        """Returns (data_dict, generation) or None."""
        if not self._ensure_client():
            return None
        try:
            blob = self._bucket.blob(self._blob_name(key))
            blob.reload()
            data = json.loads(blob.download_as_text())
            return data, blob.generation
        except Exception:
            return None

    def _sync_write_if_generation(
        self,
        key: str,
        data: dict[str, Any],
        if_generation_match: Optional[int],
    ) -> bool:
        """CAS write. Returns True on success, False on precondition failure."""
        if not self._ensure_client():
            return False
        try:
            from google.cloud.exceptions import PreconditionFailed  # type: ignore
            blob = self._bucket.blob(self._blob_name(key))
            payload = json.dumps(data).encode()
            kwargs: dict[str, Any] = {
                "content_type": "application/json",
            }
            if if_generation_match is not None:
                kwargs["if_generation_match"] = if_generation_match
            blob.upload_from_string(payload, **kwargs)
            return True
        except Exception as exc:
            logger.debug("GCS write precondition failed for %s: %s", key, exc)
            return False

    async def read(self, key: str) -> Optional[tuple[dict[str, Any], int]]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_read, key)

    async def write_cas(
        self,
        key: str,
        data: dict[str, Any],
        if_generation_match: Optional[int],
    ) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._sync_write_if_generation, key, data, if_generation_match
        )


# ---------------------------------------------------------------------------
# In-process per-key asyncio lock (optimisation on top of GCS lease)
# ---------------------------------------------------------------------------

_process_locks: dict[str, asyncio.Lock] = {}
_process_locks_mutex = asyncio.Lock()


async def _get_process_lock(key: str) -> asyncio.Lock:
    async with _process_locks_mutex:
        if key not in _process_locks:
            _process_locks[key] = asyncio.Lock()
        return _process_locks[key]


# ---------------------------------------------------------------------------
# RefreshResult — what a refresh function should return
# ---------------------------------------------------------------------------

@dataclass
class RefreshResult:
    weather_payload: Optional[dict[str, Any]]
    alerts_payload: Optional[dict[str, Any]]
    weather_ok: bool
    alerts_ok: bool


# ---------------------------------------------------------------------------
# WeatherCache — public interface
# ---------------------------------------------------------------------------

class WeatherCache:
    """
    Main cache facade.  Call `get_or_refresh(lat, lon, refresh_fn)` to get a
    CacheEntry with fresh (or stale-eligible) source payloads.

    `refresh_fn` is an async callable that takes a (lat, lon) pair and returns
    a RefreshResult.  It is called with the already-normalised coordinates.
    """

    def __init__(self, bucket_name: Optional[str] = None) -> None:
        name = bucket_name or os.getenv("WEATHER_CACHE_BUCKET", "")
        self._gcs = _GCSAdapter(name) if name else None
        self._lru = _LRU()
        if not name:
            logger.warning(
                "WEATHER_CACHE_BUCKET not set; using in-memory cache only (local dev mode)"
            )

    async def _load_entry(self, key: str) -> CacheEntry:
        """Load from LRU, then GCS, then return an empty entry."""
        lru_hit = self._lru.get(key)
        if lru_hit is not None:
            entry = CacheEntry.from_dict(lru_hit.data)
            # Trust LRU only while within source TTL to avoid stale LRU masking GCS updates
            if not entry.weather_due() and not entry.alerts_due():
                return entry

        if self._gcs is not None:
            result = await self._gcs.read(key)
            if result is not None:
                data, generation = result
                entry = CacheEntry.from_dict(data, gcs_generation=generation)
                self._lru.put(key, data)
                return entry

        return CacheEntry(
            key=key,
            weather_payload=None,
            alerts_payload=None,
            weather_retrieved_at=None,
            alerts_retrieved_at=None,
        )

    async def _acquire_lease(self, entry: CacheEntry) -> Optional[str]:
        """
        Attempt to acquire the distributed GCS lease.
        Returns lease_owner token on success, None if another owner holds the lease.
        """
        if self._gcs is None:
            # In-memory mode: always win (single-process)
            return str(uuid.uuid4())

        now = time.time()
        owner_token = str(uuid.uuid4())
        new_data = entry.to_dict()
        new_data["lease_owner"] = owner_token
        new_data["lease_expires_at"] = now + LEASE_TTL_SECONDS

        ok = await self._gcs.write_cas(
            entry.key, new_data, if_generation_match=entry.gcs_generation
        )
        if ok:
            entry.lease_owner = owner_token
            entry.lease_expires_at = now + LEASE_TTL_SECONDS
            # Re-read to get new generation
            result = await self._gcs.read(entry.key)
            if result:
                entry.gcs_generation = result[1]
            return owner_token
        return None

    async def _release_lease(self, entry: CacheEntry, owner_token: str, updated_data: Optional[dict[str, Any]]) -> None:
        """Persist final state and clear the lease."""
        if self._gcs is None:
            if updated_data:
                self._lru.put(entry.key, updated_data)
            return

        final = updated_data or entry.to_dict()
        final["lease_owner"] = None
        final["lease_expires_at"] = None
        await self._gcs.write_cas(
            entry.key, final, if_generation_match=entry.gcs_generation
        )
        if updated_data:
            self._lru.put(entry.key, final)

    async def get_or_refresh(
        self,
        lat: float,
        lon: float,
        refresh_fn: Callable[[float, float], Awaitable[RefreshResult]],
        method: str = "weather_score_v1",
    ) -> CacheEntry:
        """
        Return a CacheEntry for (lat, lon).

        - If both sources are within TTL, return immediately (no I/O).
        - If a source is due and no one holds a lease, acquire the lease and
          refresh inline (25-second total deadline).
        - If a lease is already held by another owner, return prior data with
          weather_due/alerts_due as-is so the caller can mark refresh_state=updating.
        - Respects retry backoff for consecutive failures.
        """
        q_lat = normalise_coord(lat)
        q_lon = normalise_coord(lon)
        key = cache_key(q_lat, q_lon, method)

        entry = await self._load_entry(key)

        needs_refresh = (entry.weather_due() or entry.alerts_due()) and not entry.retry_blocked()
        if not needs_refresh:
            return entry

        if entry.lease_active():
            # Another process is refreshing; return prior data
            logger.debug("Key %s has active lease; returning prior data with updating state", key)
            return entry

        # Use in-process lock to avoid duplicate refresh within this process
        proc_lock = await _get_process_lock(key)
        async with proc_lock:
            # Re-check after acquiring in-process lock
            entry = await self._load_entry(key)
            needs_refresh = (entry.weather_due() or entry.alerts_due()) and not entry.retry_blocked()
            if not needs_refresh or entry.lease_active():
                return entry

            owner_token = await self._acquire_lease(entry)
            if owner_token is None:
                # Another process just took the lease
                return await self._load_entry(key)

            # We hold the lease — perform refresh with 25-second total deadline
            updated_data: Optional[dict[str, Any]] = None
            try:
                async with asyncio.timeout(25):
                    result = await refresh_fn(q_lat, q_lon)

                now = time.time()
                if result.weather_ok and result.weather_payload is not None:
                    entry.weather_payload = result.weather_payload
                    entry.weather_retrieved_at = now
                    entry.failure_count = 0
                    entry.next_retry_eligible = None

                if result.alerts_ok and result.alerts_payload is not None:
                    entry.alerts_payload = result.alerts_payload
                    entry.alerts_retrieved_at = now

                # Partial failure: increment backoff only on weather failure
                if not result.weather_ok:
                    entry.failure_count += 1
                    entry.next_retry_eligible = now + _backoff_seconds(entry.failure_count)
                    logger.warning(
                        "Weather refresh failed for %s (attempt %d); next retry in %ds",
                        key, entry.failure_count, _backoff_seconds(entry.failure_count),
                    )

                updated_data = entry.to_dict()

            except (asyncio.TimeoutError, Exception) as exc:
                logger.error("Refresh deadline exceeded or error for %s: %s", key, exc)
                now = time.time()
                entry.failure_count += 1
                entry.next_retry_eligible = now + _backoff_seconds(entry.failure_count)
                updated_data = entry.to_dict()
            finally:
                await self._release_lease(entry, owner_token, updated_data)

            return entry
