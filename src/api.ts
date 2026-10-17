/**
 * API client for the Runway forecast service.
 *
 * - In live mode (`VITE_API_BASE_URL` set), calls the real FastAPI service.
 * - In offline / demo mode (default), generates a demo forecast in-browser
 *   using the heuristic engine so the UI always shows meaningful numbers.
 * - Every live call has a 5-second timeout and an offline fallback.
 * - PSPS/planned notices are served from a separate endpoint and never
 *   influence the unplanned-outage probability display.
 */
import type {
  ApiState,
  CountyForecast,
  CountyCoverage,
  ForecastWindow,
  PlannedOutagesResponse,
} from "./types";

// Base URL is empty string in offline mode.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const LIVE = Boolean(import.meta.env.VITE_API_BASE_URL);
const TIMEOUT_MS = 5_000;

// How many milliseconds before a cached response is considered stale.
const STALE_AFTER_MS = 60 * 60 * 1_000; // 1 hour (matches hourly batch cadence)

// -------------------------------------------------------------------------
// Low-level fetch helpers
// -------------------------------------------------------------------------

async function fetchWithTimeout(url: string): Promise<Response> {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const resp = await fetch(url, { signal: controller.signal });
    return resp;
  } finally {
    clearTimeout(id);
  }
}

async function fetchJson<T>(url: string): Promise<T> {
  const resp = await fetchWithTimeout(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status} from ${url}`);
  return resp.json() as Promise<T>;
}

// -------------------------------------------------------------------------
// Demo county registry
// -------------------------------------------------------------------------

interface DemoCounty {
  name: string;
  state: string;
  // heuristic inputs
  gustMph: number;
  tempF: number;
  soilMoisture: number;
  treeCoverPct: number;
  overheadLineShare: number;
  pastOutages3yr: number;
  medianOutageHours: number;
  alerts: string[];
}

const DEMO_COUNTIES: Record<string, DemoCounty> = {
  "06037": { name: "Los Angeles", state: "CA", gustMph: 48, tempF: 88, soilMoisture: 0.08, treeCoverPct: 18, overheadLineShare: 0.55, pastOutages3yr: 4, medianOutageHours: 5.5, alerts: ["High Wind Warning"] },
  "06059": { name: "Orange", state: "CA", gustMph: 36, tempF: 85, soilMoisture: 0.07, treeCoverPct: 12, overheadLineShare: 0.48, pastOutages3yr: 2, medianOutageHours: 4.0, alerts: [] },
  "06065": { name: "Riverside", state: "CA", gustMph: 42, tempF: 94, soilMoisture: 0.06, treeCoverPct: 10, overheadLineShare: 0.60, pastOutages3yr: 6, medianOutageHours: 7.0, alerts: [] },
  "06071": { name: "San Bernardino", state: "CA", gustMph: 44, tempF: 91, soilMoisture: 0.05, treeCoverPct: 9, overheadLineShare: 0.62, pastOutages3yr: 7, medianOutageHours: 8.0, alerts: [] },
  "06073": { name: "San Diego", state: "CA", gustMph: 31, tempF: 82, soilMoisture: 0.06, treeCoverPct: 14, overheadLineShare: 0.45, pastOutages3yr: 3, medianOutageHours: 4.5, alerts: [] },
  "06085": { name: "Santa Clara", state: "CA", gustMph: 22, tempF: 78, soilMoisture: 0.15, treeCoverPct: 28, overheadLineShare: 0.40, pastOutages3yr: 5, medianOutageHours: 6.0, alerts: [] },
  "36061": { name: "New York", state: "NY", gustMph: 18, tempF: 68, soilMoisture: 0.20, treeCoverPct: 22, overheadLineShare: 0.20, pastOutages3yr: 2, medianOutageHours: 3.5, alerts: [] },
  "48201": { name: "Harris", state: "TX", gustMph: 29, tempF: 92, soilMoisture: 0.18, treeCoverPct: 16, overheadLineShare: 0.62, pastOutages3yr: 6, medianOutageHours: 7.0, alerts: [] },
  "17031": { name: "Cook", state: "IL", gustMph: 24, tempF: 72, soilMoisture: 0.22, treeCoverPct: 20, overheadLineShare: 0.35, pastOutages3yr: 3, medianOutageHours: 4.0, alerts: [] },
  "53033": { name: "King", state: "WA", gustMph: 33, tempF: 64, soilMoisture: 0.32, treeCoverPct: 45, overheadLineShare: 0.55, pastOutages3yr: 4, medianOutageHours: 5.0, alerts: [] },
  "12086": { name: "Miami-Dade", state: "FL", gustMph: 41, tempF: 94, soilMoisture: 0.28, treeCoverPct: 30, overheadLineShare: 0.50, pastOutages3yr: 7, medianOutageHours: 8.0, alerts: [] },
  "04013": { name: "Maricopa", state: "AZ", gustMph: 26, tempF: 105, soilMoisture: 0.03, treeCoverPct: 4, overheadLineShare: 0.58, pastOutages3yr: 3, medianOutageHours: 4.5, alerts: [] },
};

// -------------------------------------------------------------------------
// Heuristic probability (mirrors risk.ts algorithm, runs inline)
// -------------------------------------------------------------------------

function clamp(v: number, lo: number, hi: number) { return Math.max(lo, Math.min(hi, v)); }

function computeDemoProb(c: DemoCounty, gustScale = 1.0, tempScale = 1.0): number {
  const gust = c.gustMph * gustScale;
  const temp = c.tempF * tempScale;
  const wetSoil = c.soilMoisture > 0.30 ? 1 : 0;
  const windScore = clamp(clamp((gust - 25) / 35, 0, 1) * (1 + 0.3 * wetSoil), 0, 1);
  const heatScore = clamp((temp - 90) / 20, 0, 1);
  const exposure = 0.4 * (c.treeCoverPct / 100) + 0.3 * c.overheadLineShare;
  const history = clamp(c.pastOutages3yr / 6, 0, 1);
  const hazard = Math.max(windScore, 0.8 * heatScore);
  let p = 1 - Math.exp(-2.2 * hazard * (0.5 + exposure) * (0.6 + 0.8 * history));
  if (c.alerts.some(a => a.toLowerCase().includes("red flag") || a.toLowerCase().includes("high wind"))) {
    p = Math.max(p, 0.6);
  }
  return clamp(p, 0.02, 0.97);
}

function buildDemoReasons(c: DemoCounty): string[] {
  const r: string[] = [];
  if (c.gustMph > 25) r.push(`Wind gusts up to ${c.gustMph} mph`);
  if (c.soilMoisture > 0.30 && c.gustMph > 25) r.push("Ground is wet, so trees fall more easily");
  if (c.tempF > 90) r.push(`Heat up to ${c.tempF}°F will strain the grid`);
  if (c.treeCoverPct > 10) r.push(`${c.treeCoverPct}% tree cover near power lines`);
  if (c.overheadLineShare > 0.3) r.push("Mostly overhead power lines");
  c.alerts.forEach(a => r.push(`${a} in effect`));
  return r.slice(0, 4);
}

function buildDemoWindow(
  label: string, startOffsetH: number, endOffsetH: number,
  c: DemoCounty, gustScale: number, tempScale: number,
  alerts: string[]
): ForecastWindow {
  const base = Date.now();
  const prob = computeDemoProb(c, gustScale, tempScale);
  const primaryDriver = c.gustMph * gustScale >= c.tempF * tempScale * 0.3 ? "wind" : "heat";
  const durLow = primaryDriver === "wind" ? Math.max(2, 0.5 * c.medianOutageHours) : 1;
  const durHigh = primaryDriver === "wind" ? Math.max(8, 1.5 * c.medianOutageHours) : 6;
  return {
    window_label: label,
    window_start: new Date(base + startOffsetH * 3600000).toISOString(),
    window_end: new Date(base + endOffsetH * 3600000).toISOString(),
    probability: prob,
    duration_low_hours: durLow,
    duration_high_hours: durHigh,
    reasons: buildDemoReasons({ ...c, gustMph: c.gustMph * gustScale, tempF: c.tempF * tempScale, alerts }),
    primary_driver: primaryDriver as "wind" | "heat",
    active_alerts: alerts,
  };
}

export function buildDemoForecast(fips: string): CountyForecast {
  const c = DEMO_COUNTIES[fips] ?? DEMO_COUNTIES["06037"];
  const now = new Date().toISOString();
  return {
    county_fips: fips,
    county_name: c.name,
    state: c.state,
    model_version: "0.0.0-demo",
    issued_at: now,
    source_timestamps: { gfs: now, nws: now },
    // Transfer estimate: shows real numbers but flags that local labels haven't been validated
    status: "transfer_estimate",
    next_24h: buildDemoWindow("Next 24h", 0, 24, c, 1.0, 1.0, c.alerts),
    next_24_48h: buildDemoWindow("24–48h", 24, 48, c, 0.75, 0.95, []),
    next_48_72h: buildDemoWindow("48–72h", 48, 72, c, 0.55, 0.90, []),
    is_stale: false,
    stale_reason: null,
    max_gust_mph: c.gustMph,
    max_temp_f: c.tempF,
  };
}

// -------------------------------------------------------------------------
// Offline fixture helper (generates demo forecast in-browser)
// -------------------------------------------------------------------------

async function loadForecastFixture(fips: string): Promise<CountyForecast> {
  return buildDemoForecast(fips);
}

async function loadPlannedOutagesFixture(
  fips: string
): Promise<PlannedOutagesResponse> {
  return {
    county_fips: fips,
    retrieved_at: new Date().toISOString(),
    no_feed_reason: "No public planned-outage feed available for this county.",
    notices: [],
  };
}

// -------------------------------------------------------------------------
// Public API functions
// -------------------------------------------------------------------------

/**
 * Fetch the three-window unplanned outage forecast for a county.
 *
 * Returns an ApiState so callers can handle loading / stale / error states
 * without any thrown exceptions.
 */
export async function fetchForecast(
  fips: string,
  previousData?: CountyForecast,
  previousFetchedAt?: number
): Promise<ApiState<CountyForecast>> {
  // If we have a recent cached result, serve it as stale while reloading.
  const now = Date.now();
  if (
    previousData &&
    previousFetchedAt &&
    now - previousFetchedAt < STALE_AFTER_MS
  ) {
    // Still fresh — no refetch needed
    return { phase: "success", data: previousData, fetchedAt: previousFetchedAt };
  }

  try {
    let data: CountyForecast;
    if (LIVE) {
      data = await fetchJson<CountyForecast>(
        `${API_BASE}/v1/forecasts/${fips}`
      );
    } else {
      data = await loadForecastFixture(fips);
    }

    // If the server itself says the batch is stale, bubble that up
    if (data.is_stale) {
      return { phase: "stale", data, fetchedAt: now };
    }
    return { phase: "success", data, fetchedAt: now };
  } catch (err) {
    // Fall back to previous data as stale if available
    if (previousData && previousFetchedAt) {
      return { phase: "stale", data: previousData, fetchedAt: previousFetchedAt };
    }
    return {
      phase: "error",
      message: err instanceof Error ? err.message : "Failed to load forecast.",
    };
  }
}

/**
 * Fetch planned/PSPS notices for a county.
 * These never influence the unplanned-outage probability.
 */
export async function fetchPlannedOutages(
  fips: string
): Promise<PlannedOutagesResponse> {
  try {
    if (LIVE) {
      return await fetchJson<PlannedOutagesResponse>(
        `${API_BASE}/v1/planned-outages/${fips}`
      );
    }
    return await loadPlannedOutagesFixture(fips);
  } catch {
    return {
      county_fips: fips,
      retrieved_at: new Date().toISOString(),
      no_feed_reason: "Planned-outage feed temporarily unavailable.",
      notices: [],
    };
  }
}

/**
 * Fetch county coverage metadata.
 */
export async function fetchCoverage(): Promise<CountyCoverage[]> {
  if (!LIVE) return [];
  try {
    return await fetchJson<CountyCoverage[]>(`${API_BASE}/v1/coverage`);
  } catch {
    return [];
  }
}

// -------------------------------------------------------------------------
// /v1/plan  types + fetch
// -------------------------------------------------------------------------

export type PlanWhen = "two_days_before" | "night_before" | "one_hour_before" | "during_outage";

export interface ApiPlanItem {
  title: string;
  title_en: string;
  when: PlanWhen;
  reason: string;
  reason_en: string;
}

export interface ApiPlanResponse {
  reply: string;
  items: ApiPlanItem[];
  source: "llm" | "fallback";
  fallback_reason?: "missing_credentials" | "project_unavailable" | "runtime_unavailable" | "model_unavailable" | "access_denied" | "watsonx_error" | null;
}

export interface PlanRequestPayload {
  message: string;
  history?: { role: "assistant" | "user"; text: string }[];
  resident: {
    name: string;
    age?: number;
    devices: string[];
    batteryWh?: number;
    isRenter?: boolean;
    livesAlone?: boolean;
    hasEV?: boolean;
    language?: string;
  };
  forecast: {
    probability: number;
    windowStart?: string;
    windowEnd?: string;
    durationLowHours?: number;
    durationHighHours?: number;
    primaryCause?: string;
    reasons?: string[];
  };
  runwayHoursAtFullCharge?: number;
  nearestCoolPlace?: string;
}

/**
 * POST /v1/plan — generate a personalised outage preparation plan.
 * Falls back gracefully to the static template response when the server
 * is unreachable or watsonx credentials are not configured.
 */
export async function fetchPlan(
  payload: PlanRequestPayload
): Promise<ApiPlanResponse> {
  const url = `${API_BASE}/v1/plan`;
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(30_000),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} from /v1/plan`);
  return resp.json() as Promise<ApiPlanResponse>;
}

// -------------------------------------------------------------------------
// Formatting helpers (used by components)
// -------------------------------------------------------------------------

/** Format a probability (0–1) as a whole-percent string, e.g. "74%" */
export function formatProbability(p: number | null): string {
  if (p === null) return "—";
  return `${Math.round(p * 100)}%`;
}

/** Format an issued_at ISO string as relative age, e.g. "3 min ago" */
export function formatForecastAge(issuedAt: string): string {
  const diff = Date.now() - new Date(issuedAt).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
}

/** Badge text for a ForecastStatus value */
export function statusBadgeText(status: CountyForecast["status"]): string {
  switch (status) {
    case "local_validated":
      return "Validated";
    case "transfer_estimate":
      return "Transfer estimate";
    case "insufficient_data":
      return "Insufficient data";
    case "unavailable":
      return "Unavailable";
  }
}
