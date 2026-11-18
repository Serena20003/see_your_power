from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from typing import Literal
import hashlib
import json
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

HOUR = timedelta(hours=1)
NODATA = -9999.0
FIELDS = ('TOT_PREC', 'T_2M', 'U_10M', 'V_10M')
WEATHER_BANDS = ('precipitation_mm', 'temperature_c', 'wind_speed_ms')
WEATHER_UNITS = ('mm', 'degC', 'm s-1')
WEATHER_SEMANTICS = ('interval_total', 'endpoint_sample', 'endpoint_sample')


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone-aware UTC time required')
    return value.astimezone(timezone.utc)


def canonical(value) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class GridSpec(Contract):
    grid_id: str = 'conus-0125-v1'
    crs: Literal['EPSG:4326'] = 'EPSG:4326'
    west: float = -125
    south: float = 24
    east: float = -66
    north: float = 50
    resolution: float = 0.125
    description: str = 'Rectangular CONUS extent; includes ocean and neighboring countries. Risk uses a separate CONUS land mask.'

    @model_validator(mode='after')
    def geometry(self):
        numbers = [self.west, self.south, self.east, self.north, self.resolution]
        if not np.isfinite(numbers).all() or self.resolution <= 0 or not (-180 <= self.west < self.east <= 180 and -90 <= self.south < self.north <= 90):
            raise ValueError('invalid grid extent')
        for span in [self.east-self.west, self.north-self.south]:
            if abs(span/self.resolution-round(span/self.resolution)) > 1e-8:
                raise ValueError('extent must align to cell resolution')
        return self

    @property
    def width(self): return round((self.east-self.west)/self.resolution)
    @property
    def height(self): return round((self.north-self.south)/self.resolution)
    @property
    def shape(self): return (self.height, self.width)
    @property
    def transform(self):
        from affine import Affine
        return Affine(self.resolution, 0, self.west, 0, -self.resolution, self.north)

    def center(self, row, col):
        return (self.west+(col+.5)*self.resolution, self.north-(row+.5)*self.resolution)


class SourceField(Contract):
    run: datetime
    field: Literal['TOT_PREC', 'T_2M', 'U_10M', 'V_10M']
    start_step: int = Field(ge=0)
    end_step: int = Field(ge=0)
    first_seen: datetime
    url: str
    grid_id: str
    units: str
    level: int
    level_type: str
    packing: str
    vector_reference: str = 'earth'
    sha256: str | None = None
    retrieved_at: datetime | None = None
    path: str | None = None
    metadata: dict = Field(default_factory=dict)

    @field_validator('run', 'first_seen', 'retrieved_at')
    @classmethod
    def times(cls, v): return utc(v) if v is not None else None

    @model_validator(mode='after')
    def bounds(self):
        if self.run.minute or self.run.second or self.run.microsecond or self.start_step > self.end_step:
            raise ValueError('invalid forecast step/run alignment')
        return self


class InputInterval(Contract):
    start: datetime
    end: datetime
    run: datetime
    @field_validator('start', 'end', 'run')
    @classmethod
    def times(cls,v): return utc(v)


class SourceSelection(Contract):
    cutoff: datetime
    target_start: datetime
    selected_run: datetime
    intervals: list[InputInterval]
    fields: list[SourceField]
    missing_history: list[datetime] = Field(default_factory=list)


class InputInventory(Contract):
    selection: SourceSelection
    fields: list[SourceField]
    tool_versions: dict[str,str] = Field(default_factory=dict)
    remaps: list[dict] = Field(default_factory=list)
    downloaded_bytes: int = 0


@dataclass
class WeatherCube:
    grid: GridSpec
    # Arrays may be np.memmap; each slice has shape [3,y,x].
    values: np.ndarray
    intervals: list[InputInterval]
    valid: np.ndarray
    provenance: list[dict] = dc_field(default_factory=list)


@dataclass
class WeatherWindow:
    target: InputInterval
    values: np.ndarray
    valid: np.ndarray
    intervals: list[InputInterval | None]
    provenance: list[dict]


@dataclass
class StaticContext:
    grid: GridSpec
    version: str
    canopy: np.ndarray
    valid_land_fraction: np.ndarray
    analysis_mask: np.ndarray
    exposure: np.ndarray
    coverage: np.ndarray
    manifest: dict = dc_field(default_factory=dict)


class StaticManifest(Contract):
    schema_version: str = 'raster-static/v1'
    static_version: str
    grid: GridSpec
    objects: dict[str,dict]
    sources: dict
    recipe: dict
    fixture: bool = False


@dataclass
class RiskResult:
    score: np.ndarray
    valid: np.ndarray
    diagnostics: dict[str,np.ndarray]
    metadata: dict = dc_field(default_factory=lambda: {
        'value_kind':'heuristic_score', 'calibrated':False,
        'asset_conditioned':False, 'method':'environmental_heuristic_v2'})
