/**
 * risk.ts — illustrative South Bay demo forecast engine (specs.md §8.1)
 *
 * This is a transparent local-demo heuristic.  It is NOT the nationwide
 * production predictor.  Before showing output as a calibrated "% chance",
 * replace with the validated output from GET /v1/forecasts/{fips}.
 */

import type { FireZone } from "../types";

// -------------------------------------------------------------------------
// Input types
// -------------------------------------------------------------------------

export interface WeatherHour {
  validTime: Date;
  windGustMph: number;
  tempF: number;
  soilMoisture: number; // m³/m³
}

export interface WeatherAlert {
  event: string;
  onset: Date;
  expires: Date;
}

export interface RiskInputs {
  // Neighborhood physical features
  treeCoverPct: number;        // 0–100
  overheadLineShare: number;   // 0–1
  fireZone: FireZone;
  pastOutages3yr: number;      // unplanned only
  medianOutageHours: number;

  // Weather
  weatherHours: WeatherHour[];
  alerts: WeatherAlert[];

  // Simulation time
  simTime: Date;
}

// -------------------------------------------------------------------------
// Output type
// -------------------------------------------------------------------------

export interface Forecast {
  probability: number;          // 0–1, clipped to 0.02–0.97
  windowStart: Date;
  windowEnd: Date;
  durationLowHours: number;
  durationHighHours: number;
  primaryCause: "wind" | "heat";
  reasons: string[];            // top 4
  // Raw scores for display
  windScore: number;
  heatScore: number;
  maxGustMph: number;
  maxTempF: number;
}

// -------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function addHours(d: Date, h: number): Date {
  return new Date(d.getTime() + h * 3_600_000);
}

// -------------------------------------------------------------------------
// Main engine (specs.md §8.1 steps 1–16)
// -------------------------------------------------------------------------

export function computeForecast(inputs: RiskInputs): Forecast {
  const { weatherHours, alerts, simTime } = inputs;

  // 1. Max values over next 24 h
  const window = weatherHours.filter(
    (h) => h.validTime >= simTime && h.validTime < addHours(simTime, 24)
  );

  if (window.length === 0) {
    // No weather data — return minimal safe response
    return {
      probability: 0.02,
      windowStart: simTime,
      windowEnd: addHours(simTime, 24),
      durationLowHours: 2,
      durationHighHours: 8,
      primaryCause: "wind",
      reasons: ["Forecast unavailable — weather data missing"],
      windScore: 0,
      heatScore: 0,
      maxGustMph: 0,
      maxTempF: 60,
    };
  }

  const gusts = window.map((h) => h.windGustMph);
  const temps = window.map((h) => h.tempF);
  const maxGust = Math.max(...gusts);
  const maxTemp = Math.max(...temps);
  const gustHour = window[gusts.indexOf(maxGust)].validTime;
  const tempHour = window[temps.indexOf(maxTemp)].validTime;

  // 2. Wet soil
  const maxSoil = Math.max(...window.map((h) => h.soilMoisture));
  const wetSoil = maxSoil > 0.30 ? 1 : 0;

  // 3–4. Scores
  const rawWind = clamp((maxGust - 25) / 35, 0, 1);
  const windScore = clamp(rawWind * (1 + 0.3 * wetSoil), 0, 1);
  const heatScore = clamp((maxTemp - 90) / 20, 0, 1);

  // 5. Fire factor
  const fireFactor: Record<FireZone, number> = { none: 0, tier2: 0.6, tier3: 1.0 };

  // 6. Exposure
  const exposure =
    0.4 * (inputs.treeCoverPct / 100) +
    0.3 * inputs.overheadLineShare +
    0.3 * fireFactor[inputs.fireZone];

  // 7. History (unplanned only)
  const history = clamp(inputs.pastOutages3yr / 6, 0, 1);

  // 8–9. Base probability
  const hazard = Math.max(windScore, 0.8 * heatScore);
  let p = 1 - Math.exp(-2.2 * hazard * (0.5 + exposure) * (0.6 + 0.8 * history));

  // 10. Alert floor
  const activeAlerts = alerts.filter(
    (a) => a.onset < addHours(simTime, 24) && a.expires > simTime
  );
  const hasRedFlag = activeAlerts.some((a) =>
    a.event.toLowerCase().includes("red flag")
  );
  const hasHighWind = activeAlerts.some((a) =>
    a.event.toLowerCase().includes("high wind")
  );
  if (hasRedFlag || hasHighWind) p = Math.max(p, 0.6);

  // 11. Clamp
  p = clamp(p, 0.02, 0.97);

  // 12–13. Primary cause + window
  const primaryCause: "wind" | "heat" =
    windScore >= 0.8 * heatScore ? "wind" : "heat";
  const center = primaryCause === "wind" ? gustHour : tempHour;
  const windowStart = addHours(center, -2);
  const windowEnd = addHours(center, 2);

  // 14. Duration
  const durationLowHours =
    primaryCause === "wind"
      ? Math.max(2, 0.5 * inputs.medianOutageHours)
      : 1;
  const durationHighHours =
    primaryCause === "wind"
      ? Math.max(8, 1.5 * inputs.medianOutageHours)
      : 6;

  // 15. Reasons (top 4 by contribution weight)
  const candidates: [number, string][] = [];
  if (windScore > 0)
    candidates.push([windScore, `Wind gusts up to ${Math.round(maxGust)} mph`]);
  if (wetSoil && windScore > 0)
    candidates.push([0.3 * wetSoil * windScore, "Ground is wet, so trees fall more easily"]);
  if (heatScore > 0)
    candidates.push([heatScore, `Heat up to ${Math.round(maxTemp)}°F will strain the grid`]);
  if (inputs.treeCoverPct > 0)
    candidates.push([
      0.4 * (inputs.treeCoverPct / 100),
      `${Math.round(inputs.treeCoverPct)}% tree cover near power lines`,
    ]);
  if (inputs.overheadLineShare > 0.3)
    candidates.push([0.3 * inputs.overheadLineShare, "Mostly overhead power lines"]);
  if (inputs.fireZone === "tier3")
    candidates.push([0.3, "Tier 3 fire zone"]);
  else if (inputs.fireZone === "tier2")
    candidates.push([0.18, "Tier 2 fire zone"]);
  if (inputs.pastOutages3yr > 0)
    candidates.push([history, `${inputs.pastOutages3yr} outages here in the last 3 years`]);
  activeAlerts.forEach((a) =>
    candidates.push([0.8, `${a.event} in effect`])
  );

  candidates.sort((a, b) => b[0] - a[0]);
  const reasons = candidates.slice(0, 4).map(([, text]) => text);

  return {
    probability: p,
    windowStart,
    windowEnd,
    durationLowHours,
    durationHighHours,
    primaryCause,
    reasons,
    windScore,
    heatScore,
    maxGustMph: maxGust,
    maxTempF: maxTemp,
  };
}
