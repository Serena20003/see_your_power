"""
Shared data types for the Runway forecast pipeline and API.

These Pydantic models define the contract between:
  - pipeline/ (feature engineering)
  - inference/ (batch forecast generation)
  - service/ (FastAPI read layer)
  - tests/
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ForecastStatus(str, Enum):
    """Coverage / validation status attached to every county forecast."""
    LOCAL_VALIDATED = "local_validated"
    TRANSFER_ESTIMATE = "transfer_estimate"
    INSUFFICIENT_DATA = "insufficient_data"
    UNAVAILABLE = "unavailable"


class DataMode(str, Enum):
    """
    Provenance of the returned data.  Independent of model eligibility status.
    - sample:      data originates from a fixture or synthetic source
    - live:        data was retrieved from a live provider (may still be stale)
    - unavailable: no usable data could be returned
    """
    SAMPLE = "sample"
    LIVE = "live"
    UNAVAILABLE = "unavailable"


class OutageType(str, Enum):
    UNPLANNED = "unplanned"
    PLANNED = "planned"
    PSPS = "psps"
    UNKNOWN = "unknown"


class PrimaryDriver(str, Enum):
    WIND = "wind"
    HEAT = "heat"
    COMBINED = "combined"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Forecast window (one of three rolling 24-hour bands)
# ---------------------------------------------------------------------------

class ForecastWindow(BaseModel):
    """A single 24-hour forecast window."""

    window_label: str = Field(
        description="Human-readable label, e.g. 'Next 24h', '24–48h', '48–72h'"
    )
    window_start: datetime
    window_end: datetime

    # Calibrated onset probability (0–1).  None when insufficient data.
    probability: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Calibrated probability of ≥1 new unplanned outage in this county during this window."
    )

    # Conditional duration (only populated when probability is not None)
    duration_low_hours: Optional[float] = Field(default=None, ge=0.0)
    duration_high_hours: Optional[float] = Field(default=None, ge=0.0)

    # Top reasons driving this window's probability (max 4)
    reasons: list[str] = Field(default_factory=list, max_length=4)

    primary_driver: PrimaryDriver = PrimaryDriver.UNKNOWN

    # Alert triggers (e.g. active NWS Red Flag Warning)
    active_alerts: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Full county forecast (all three windows)
# ---------------------------------------------------------------------------

class CountyForecast(BaseModel):
    """Full forecast payload for one county FIPS, served by GET /v1/forecasts/{fips}."""

    county_fips: str = Field(min_length=5, max_length=5)
    county_name: str
    state: str

    model_version: str
    issued_at: datetime
    source_timestamps: dict[str, datetime] = Field(
        default_factory=dict,
        description="Timestamps of the most recent raw input batches used for this issue."
    )

    status: ForecastStatus

    # Provenance: sample fixtures are always "sample"; live pipeline output is "live";
    # unknown/unavailable counties are "unavailable".
    # Old JSON without this field defaults conservatively to "unavailable".
    data_mode: DataMode = Field(
        default=DataMode.UNAVAILABLE,
        description="Data provenance: sample | live | unavailable."
    )

    # The three windows.  Always present; probability=None when status != local_validated/transfer.
    next_24h: ForecastWindow
    next_24_48h: ForecastWindow
    next_48_72h: ForecastWindow

    # True when the previous complete batch is being served because the current batch failed.
    is_stale: bool = False
    stale_reason: Optional[str] = None

    # Weather features used (for transparency / display)
    max_gust_mph: Optional[float] = None
    max_temp_f: Optional[float] = None


# ---------------------------------------------------------------------------
# Planned-outage notice (separate source-backed layer, never model input)
# ---------------------------------------------------------------------------

class PlannedOutageNotice(BaseModel):
    source: str
    source_last_updated: datetime
    notice_type: str  # "planned_maintenance" | "psps"
    utility: str
    affected_description: str
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    reason: Optional[str] = None


class PlannedOutagesResponse(BaseModel):
    county_fips: str
    retrieved_at: datetime
    # None means feed is present but returned no notices; string means no usable feed.
    no_feed_reason: Optional[str] = None
    notices: list[PlannedOutageNotice] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Coverage metadata
# ---------------------------------------------------------------------------

class CountyCoverage(BaseModel):
    county_fips: str
    county_name: str
    state: str
    has_local_labels: bool
    label_years: list[int] = Field(default_factory=list)
    transfer_source_states: list[str] = Field(default_factory=list)
    gfs_coverage: bool = True
    nws_alerts_coverage: bool = True
    canopy_coverage: bool
    terrain_coverage: bool
    current_status: ForecastStatus


# ---------------------------------------------------------------------------
# Weather score — separate contract, never reuses CountyForecast fields
# ---------------------------------------------------------------------------

class WeatherScoreMethod(str, Enum):
    V1 = "weather_score_v1"


class AlertStatus(str, Enum):
    AVAILABLE = "available"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class RefreshState(str, Enum):
    IDLE = "idle"
    UPDATING = "updating"
    RETRY_WAIT = "retry_wait"


class NWSAlertItem(BaseModel):
    """One active NWS alert near the queried point."""
    id: str
    event: str
    effective: Optional[datetime] = None
    onset: Optional[datetime] = None
    expires: Optional[datetime] = None
    url: Optional[str] = None


class AlertsContext(BaseModel):
    """
    Top-level NWS alert context.  Failure/empty/stale are distinct states:
    - available + items=[]: live response returned no active alerts
    - stale:    cached response older than 5 minutes but under 15
    - unavailable: no usable live data; never implies no active alerts exist
    """
    status: AlertStatus
    retrieved_at: Optional[datetime] = None
    items: list[NWSAlertItem] = Field(default_factory=list)


class WeatherDriver(BaseModel):
    """One numerical driver explanation for a window score."""
    label: str
    value: float
    unit: str


class WeatherWindow(BaseModel):
    """
    A single 24-hour weather forecast window (0–24h, 24–48h, 48–72h from retrieval anchor).
    score is null when fewer than 24 complete hourly records are available.
    """
    window_start: datetime
    window_end: datetime

    # 0–100 weather stress score, null for incomplete windows
    score: Optional[float] = Field(default=None, ge=0.0, le=100.0)

    # Measurements — always in SI units as received from Open-Meteo
    peak_wind_speed_ms: Optional[float] = None   # m/s, NOT wind gust
    min_temp_c: Optional[float] = None
    max_temp_c: Optional[float] = None
    mean_precip_mm: Optional[float] = None

    # Up to three numerical driver explanations
    drivers: list[WeatherDriver] = Field(default_factory=list, max_length=3)


class WeatherScoreResponse(BaseModel):
    """
    Response from GET /v1/weather-score?lat=…&lon=…

    data_mode:
      live      — verified live-source provenance (may still be stale)
      unavailable — no usable live data remains

    calibrated is always False; this is a heuristic weather indicator.
    """
    # Coordinates
    requested_lat: float
    requested_lon: float
    queried_lat: float    # rounded to 3 decimal places
    queried_lon: float
    # Provider-returned grid coordinates and elevation (from Open-Meteo)
    grid_lat: Optional[float] = None
    grid_lon: Optional[float] = None
    grid_elevation_m: Optional[float] = None

    method: WeatherScoreMethod = WeatherScoreMethod.V1
    data_mode: DataMode      # live | unavailable
    calibrated: bool = False

    # Timestamps
    weather_retrieved_at: Optional[datetime] = None
    provider_issue_time: Optional[datetime] = None   # always null; Open-Meteo has no issue time

    # Availability/freshness flags
    weather_available: bool = False
    weather_stale: bool = False          # True when >60 min old but <6 h old
    refresh_state: RefreshState = RefreshState.IDLE
    next_refresh_at: datetime

    # Three forecast windows (0–24, 24–48, 48–72 from retrieval anchor)
    windows: list[WeatherWindow] = Field(default_factory=list)

    # Alerts context — independent of score and windows
    alerts: AlertsContext
