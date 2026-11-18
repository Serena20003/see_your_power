"""Versioned immutable records. Metadata is never admitted as a predictor."""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Literal, Protocol
from dataclasses import dataclass
import numpy as np
from pydantic import Field, ConfigDict, field_validator, model_validator
from raster_pipeline.contracts import Contract, GridSpec, utc, canonical, digest

HORIZONS = ('0_24', '24_48', '48_72')
ROLES = ('train', 'tune', 'calibrate', 'gate', 'test')


def time(value):
    return utc(datetime.fromisoformat(value.replace('Z', '+00:00'))) if isinstance(value,str) else utc(value)


class DataReadinessError(ValueError):
    """Usable real evidence is missing; never substitute fabricated data."""


class Store(Protocol):
    def read(self, key: str) -> tuple[bytes | None, str | None]: ...
    def put(self, key: str, data: bytes) -> None: ...
    def replace_pointer(self, key: str, data: bytes, expected_generation: str | None) -> bool: ...


class RowKey(Contract):
    archive_id: str
    grid_id: str
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    horizon_id: Literal['0_24','24_48','48_72']

    @property
    def id(self): return digest(self)


class RawSnapshot(Contract):
    source: str
    uri: str
    retrieved_at: datetime
    source_updated_at: datetime | None = None
    snapshot_id: str
    raw_ref: dict
    schema_hash: str
    adapter_version: str
    complete_pages: bool
    fixture: bool = False
    evidence: dict
    @field_validator('retrieved_at','source_updated_at')
    @classmethod
    def times(cls,v): return time(v) if v is not None else None


class OutageEvent(Contract):
    canonical_id: str
    source: str
    utility_id: str
    original_event_id: str
    onset: datetime | None = None
    onset_upper: datetime | None = None
    onset_quality: str = 'unknown'
    first_seen: datetime
    last_seen: datetime
    outage_type: Literal['unplanned','planned','psps','unknown'] = 'unknown'
    type_evidence: str = ''
    longitude: float | None = None
    latitude: float | None = None
    geometry_crs: Literal['EPSG:4326'] = 'EPSG:4326'
    geometry_meaning: str = 'unknown'
    location_quality: str = 'unknown'
    county: str | None = None
    snapshot_ids: tuple[str,...]
    @field_validator('onset','onset_upper','first_seen','last_seen')
    @classmethod
    def times(cls,v): return time(v) if v is not None else None


class CoverageInterval(Contract):
    coverage_id: str
    source: str
    domain_id: str
    start: datetime
    end: datetime
    footprint_version: str
    # Reviewed whole-supported-land cells, including the complete utility union.
    cells: tuple[tuple[str,int,int],...] = ()
    expected_polls: int = Field(ge=1)
    received_polls: int = Field(ge=0)
    complete: bool = False
    whole_cell: bool = False
    ascertainment_audited: bool = False
    limitations: tuple[str,...] = ()
    evidence: tuple[str,...]
    @field_validator('start','end')
    @classmethod
    def times(cls,v): return time(v)
    @model_validator(mode='after')
    def bounds(self):
        if self.end<=self.start: raise ValueError('empty coverage interval')
        if self.complete and (self.received_polls<self.expected_polls or not self.evidence):
            raise ValueError('complete coverage requires all polls and evidence')
        return self


class LabelPolicy(Contract):
    version: str
    reporting_lag_hours: float = Field(ge=0)
    accepted_geometry_meanings: tuple[str,...] = ('qualified_incident_marker',)
    source_priority: tuple[str,...] = ()
    duplicate_aliases: dict[str,str] = Field(default_factory=dict)


class EventLedger(Contract):
    events: tuple[OutageEvent,...]
    quarantine: tuple[dict,...]
    policy_hash: str


class LabelRow(Contract):
    key: RowKey
    start: datetime
    end: datetime
    label: Literal[0,1] | None
    new_event_count: int = Field(ge=0)
    coverage_ids: tuple[str,...]
    event_ids: tuple[str,...]
    exclusion_reason: str | None
    mature_at: datetime
    freeze_time: datetime
    domain_id: str
    state: str
    region: str
    block_id: str
    season: str
    @field_validator('start','end','mature_at','freeze_time')
    @classmethod
    def times(cls,v): return time(v)


class ArchiveRef(Contract):
    archive_id: str
    manifest_key: str
    sha256: str
    fixture: bool
    normalization_reproducible: bool


@dataclass(frozen=True)
class ArchivedBatch:
    ref: ArchiveRef
    manifest: dict
    grid: GridSpec
    weather: np.ndarray
    static: object
    fallback: np.ndarray
    provenance: dict


class FeatureTable(Contract):
    model_config = ConfigDict(extra='forbid', frozen=True, arbitrary_types_allowed=True)
    names: tuple[str,...]
    values: np.ndarray
    keys: tuple[RowKey,...]
    eligible: np.ndarray
    metadata: tuple[dict,...]
    provenance: dict
    fixture: bool
    @model_validator(mode='after')
    def shape(self):
        n=len(self.keys)
        if self.values.shape!=(n,len(self.names)) or self.eligible.shape!=(n,) or len(self.metadata)!=n:
            raise ValueError('feature dimensions mismatch')
        if len({k.id for k in self.keys})!=n: raise ValueError('duplicate row key')
        if not np.isfinite(self.values[self.eligible]).all(): raise ValueError('nonfinite eligible predictors')
        self.values.setflags(write=False); self.eligible.setflags(write=False)
        return self


class DatasetSnapshot(Contract):
    model_config = ConfigDict(extra='forbid', frozen=True, arbitrary_types_allowed=True)
    dataset_id: str
    features: FeatureTable
    labels: np.ndarray
    rows: tuple[dict,...]
    exclusions: tuple[dict,...]
    freeze_time: datetime
    lineage: dict


class SplitPolicy(Contract):
    version: str
    # Exact chronological half-open boundaries, not inferred from row counts.
    boundaries: dict[str,tuple[datetime,datetime]]
    embargo_hours: int = Field(default=72,ge=72)
    held_out_states: tuple[str,...] = ()
    held_out_domains: tuple[str,...] = ()
    reviewed_blocks_hash: str


class SplitManifest(Contract):
    schema_version: str = 'outage-splits/v1'
    dataset_id: str
    policy: SplitPolicy
    assignments: dict[str,str]
    exclusions: dict[str,str]


class CalibrationBundle(Contract):
    slope: float = Field(ge=0)
    intercept: float
    offsets: dict[str,float] = Field(default_factory=dict)
    penalty: float | None = None
    counts: dict = Field(default_factory=dict)
    selection: dict = Field(default_factory=dict)


class ReleaseEvidence(Contract):
    schema_version: str = 'outage-release/v1'
    dataset_id: str
    policy_hash: str
    candidate_hash: str
    split_hash: str
    role: Literal['gate','test']
    scopes: tuple[dict,...]
    metrics: dict
    passed: bool
    fixture: bool
    failures: tuple[str,...] = ()


class ModelBundle(Contract):
    schema_version: str = 'outage-model/v1'
    model_id: str
    feature_names: tuple[str,...]
    feature_registry_hash: str
    grid_id: str
    static_version: str
    canopy_vintage: str
    trees: dict[str,str]
    calibrations: dict[str,CalibrationBundle]
    baselines: dict
    support: dict
    evidence: ReleaseEvidence | None = None
    fixture: bool
    training: dict


class ModelRef(Contract):
    model_id: str
    manifest_key: str
    sha256: str


class PredictionBatch(Contract):
    schema_version: str = 'outage-prediction/v1'
    batch_id: str
    archive: ArchiveRef
    model: ModelRef
    cutoff: datetime
    target_start: datetime
    selected_run: datetime
    published_at: datetime
    grid: GridSpec
    windows: tuple[dict,...]
    features: dict
    explanations: tuple[dict,...]
    fixture: bool
    stale_after_hours: float = 2
    fallback_recipe: dict
    @field_validator('cutoff','target_start','selected_run','published_at')
    @classmethod
    def times(cls,v): return time(v)
    @model_validator(mode='after')
    def windows_valid(self):
        if len(self.windows)!=3: raise ValueError('three windows required')
        for i,w in enumerate(self.windows):
            if w['horizon_id']!=HORIZONS[i] or time(w['start'])!=self.target_start+timedelta(hours=i*24) or time(w['end'])!=self.target_start+timedelta(hours=(i+1)*24):
                raise ValueError('window bounds/order mismatch')
        return self


class BaselineBundle(Contract):
    models: dict
    feature_names: tuple[str,...]
    training: dict


class TreeBundle(Contract):
    models: dict[str,str]
    feature_names: tuple[str,...]
    records: dict
    trials: tuple[dict,...]
    training: dict


class FeatureRow(Contract):
    key: RowKey
    names: tuple[str,...]
    values: tuple[float,...]
    region: str
    provenance: dict
    status: str
