// src/types.ts
// TypeScript types mirroring the Python shared/types.py Pydantic models.
// This is the single source of truth for the React frontend API contract.

export type DeviceType =
  | "oxygen_concentrator"
  | "cpap"
  | "home_dialysis"
  | "insulin_fridge"
  | "power_wheelchair"
  | "none";

export type FireZone = "none" | "tier2" | "tier3";

export type ForecastStatus =
  | "local_validated"
  | "transfer_estimate"
  | "insufficient_data"
  | "unavailable";

export type PrimaryDriver = "wind" | "heat" | "combined" | "unknown";

// -------------------------------------------------------------------------
// Forecast window (one 24-hour band)
// -------------------------------------------------------------------------

export interface ForecastWindow {
  window_label: string;        // "Next 24h" | "24–48h" | "48–72h"
  window_start: string;        // ISO string
  window_end: string;
  /** Calibrated probability 0–1, null when status is insufficient_data */
  probability: number | null;
  duration_low_hours: number | null;
  duration_high_hours: number | null;
  reasons: string[];
  primary_driver: PrimaryDriver;
  active_alerts: string[];
}

// -------------------------------------------------------------------------
// Full county forecast (all three windows)
// -------------------------------------------------------------------------

export interface CountyForecast {
  county_fips: string;
  county_name: string;
  state: string;
  model_version: string;
  issued_at: string;            // ISO string
  source_timestamps: Record<string, string>;
  status: ForecastStatus;
  next_24h: ForecastWindow;
  next_24_48h: ForecastWindow;
  next_48_72h: ForecastWindow;
  is_stale: boolean;
  stale_reason: string | null;
  max_gust_mph: number | null;
  max_temp_f: number | null;
}

// -------------------------------------------------------------------------
// Planned outage notices (separate layer, never model input)
// -------------------------------------------------------------------------

export interface PlannedOutageNotice {
  source: string;
  source_last_updated: string;
  notice_type: string;
  utility: string;
  affected_description: string;
  start_time: string | null;
  end_time: string | null;
  reason: string | null;
}

export interface PlannedOutagesResponse {
  county_fips: string;
  retrieved_at: string;
  no_feed_reason: string | null;
  notices: PlannedOutageNotice[];
}

// -------------------------------------------------------------------------
// County coverage metadata
// -------------------------------------------------------------------------

export interface CountyCoverage {
  county_fips: string;
  county_name: string;
  state: string;
  has_local_labels: boolean;
  label_years: number[];
  transfer_source_states: string[];
  gfs_coverage: boolean;
  nws_alerts_coverage: boolean;
  canopy_coverage: boolean;
  terrain_coverage: boolean;
  current_status: ForecastStatus;
}

// -------------------------------------------------------------------------
// API call state envelope (used by the React hook)
// -------------------------------------------------------------------------

export type ApiState<T> =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "success"; data: T; fetchedAt: number }
  | { phase: "stale"; data: T; fetchedAt: number }
  | { phase: "error"; message: string };

// -------------------------------------------------------------------------
// Resident / simulation types (for the planning layer)
// -------------------------------------------------------------------------

export interface Contact {
  name: string;
  relation: "family" | "neighbor" | "caregiver";
  phone: string;
  answerProbability: number;
}

export interface Resident {
  id: string;
  name: string;
  age: number;
  lat: number;
  lon: number;
  countyFips: string;
  neighborhoodId: string;
  cellZoneId: string;
  devices: DeviceType[];
  deviceWattsOverride?: number;
  batteryWh: number;
  initialSocPct: number;
  hasEV: boolean;
  isRenter: boolean;
  livesAlone: boolean;
  language: "en" | "es" | "ko" | "tl" | "zh";
  phone: string;
  answerProbability: number;
  contacts: Contact[];
  isSample: true;
}

export interface Neighborhood {
  id: string;
  countyFips: string;
  name: string;
  centroidLat: number;
  centroidLon: number;
  treeCoverPct: number;
  overheadLineShare: number;
  fireZone?: FireZone;
  pastOutages3yr: number;
  medianOutageHours: number;
  nearestCoolPlace: string;
}
