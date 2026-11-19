/**
 * ForecastPanel.tsx — three-window county forecast panel.
 *
 * Replaces the static mockModel `days[]` tabs with live/fixture API data.
 * Renders:
 *   - County selector
 *   - Three 24-hour window tabs: Next 24h / 24–48h / 48–72h
 *   - Forecast card: probability, duration, reasons, status badge, freshness
 *   - Loading skeleton, stale notice, error fallback
 *   - Transfer-estimate badge
 *   - Planned-outage notices (separate layer, never model input)
 */
import { useState } from "react";
import {
  InlineNotification,
  Loading,
  SkeletonText,
  Tag,
  Tile,
} from "@carbon/react";
import { ArrowRight, CloudMonitoring, WarningAlt } from "@carbon/icons-react";
import CountyRiskMap from "./CountyRiskMap";
import { useForecast } from "./useForecast";
import { formatForecastAge, formatProbability, statusBadgeText } from "./api";
import type { CountyForecast, ForecastWindow } from "./types";

// -------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------

/** Returns a hex color for a 0–1 probability (matches the map dot palette). */
function riskColor(prob: number | null): string {
  if (prob === null) return "#8d8d8d";
  if (prob < 0.35)   return "#198038"; // low    – green
  if (prob < 0.60)   return "#b28600"; // medium – amber
  if (prob < 0.80)   return "#ff832b"; // high   – orange
  return "#da1e28";                    // severe – red
}

// -------------------------------------------------------------------------
// Window tab
// -------------------------------------------------------------------------

const WINDOW_KEYS: Array<keyof Pick<CountyForecast, "next_24h" | "next_24_48h" | "next_48_72h">> = [
  "next_24h",
  "next_24_48h",
  "next_48_72h",
];

interface WindowTabsProps {
  forecast: CountyForecast;
  activeKey: typeof WINDOW_KEYS[number];
  onSelect: (key: typeof WINDOW_KEYS[number]) => void;
}

function WindowTabs({ forecast, activeKey, onSelect }: WindowTabsProps) {
  return (
    <div className="window-tabs" role="tablist" aria-label="Forecast window">
      {WINDOW_KEYS.map((key) => {
        const w: ForecastWindow = forecast[key];
        const prob = w.probability;
        const impact = w.estimated_customer_impact;
        const pct = impact != null ? Math.round(impact).toLocaleString() : prob !== null ? `${Math.round(prob * 100)}%` : "—";
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={key === activeKey}
            className={key === activeKey ? "selected" : ""}
            onClick={() => onSelect(key)}
          >
            <span className="window-tab-label">
              <span
                className="window-tab-dot"
                style={{ background: riskColor(prob) }}
                aria-hidden="true"
              />
              {w.window_label}
            </span>
            <strong>{pct}</strong>
            <span>
              {impact != null ? "estimated impact" : prob !== null
                ? `${pct} est. chance`
                : forecast.status === "insufficient_data"
                ? "Awaiting validated data"
                : "Unavailable"}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// -------------------------------------------------------------------------
// Status badge
// -------------------------------------------------------------------------

function StatusBadge({ status }: { status: CountyForecast["status"] }) {
  const type: Record<CountyForecast["status"], "blue" | "teal" | "warm-gray" | "gray"> = {
    experimental: "warm-gray",
    local_validated: "teal",
    transfer_estimate: "blue",
    insufficient_data: "warm-gray",
    unavailable: "gray",
  };
  return (
    <Tag type={type[status]} size="sm" title={statusBadgeText(status)}>
      {statusBadgeText(status)}
    </Tag>
  );
}

// -------------------------------------------------------------------------
// Forecast detail card
// -------------------------------------------------------------------------

interface ForecastCardProps {
  forecast: CountyForecast;
  windowKey: typeof WINDOW_KEYS[number];
  isStale: boolean;
}

function ForecastCard({ forecast, windowKey, isStale }: ForecastCardProps) {
  const w: ForecastWindow = forecast[windowKey];
  const prob = w.probability;
  const impact = w.estimated_customer_impact;
  const experimental = forecast.experimental_impact;
  const probPct = formatProbability(prob);
  const age = formatForecastAge(forecast.issued_at);

  const riskTag =
    prob !== null && prob >= 0.5 ? "Plan ahead" : prob !== null ? "Watchful" : null;
  const riskType: "warm-gray" | "teal" = prob !== null && prob >= 0.5 ? "warm-gray" : "teal";

  return (
    <Tile className="forecast-card" role="region" aria-labelledby="forecast-card-title">
      <div className="panel-topline">
        <span className="section-kicker">
          {forecast.county_name.toUpperCase()} · {forecast.state} · {w.window_label.toUpperCase()}
        </span>
        <div className="forecast-badges">
          <StatusBadge status={forecast.status} />
          {riskTag && <Tag type={riskType} size="sm">{riskTag}</Tag>}
        </div>
      </div>

      {/* Probability display */}
      {impact != null ? (
        <>
          <h2 id="forecast-card-title" className="forecast-prob">≈{Math.round(impact).toLocaleString()}</h2>
          <p className="forecast-prob-label">estimated county customer impact · experimental</p>
          <p>{new Date(w.window_start).toLocaleDateString(undefined, { timeZone: "UTC" })} · 00:00–24:00 UTC</p>
          <p>Calendar and geography baseline trained on {experimental?.history_year} records. Live weather is shown separately.</p>
        </>
      ) : prob !== null ? (
        <>
          <h2 id="forecast-card-title" className="forecast-prob">{probPct}</h2>
          <p className="forecast-prob-label">estimated chance of a new unplanned outage in this county</p>
        </>
      ) : (
        <>
          <h2 id="forecast-card-title" className="forecast-no-data">—</h2>
          <p className="forecast-no-data-label">
            {experimental && !experimental.supported
              ? "This county is outside the existing model's supported coverage. No estimate is substituted."
              : forecast.status === "insufficient_data"
              ? "Numeric forecast not yet available — release checks not yet passed for this county."
              : forecast.status === "transfer_estimate"
              ? "Transfer estimate — no local labels available."
              : "Forecast unavailable for this county."}
          </p>
        </>
      )}

      {/* Active alerts */}
      {w.active_alerts.length > 0 && (
        <div className="forecast-alerts">
          {w.active_alerts.map((a) => (
            <span key={a} className="forecast-alert-pill">
              <WarningAlt size={14} /> {a}
            </span>
          ))}
        </div>
      )}

      {/* Reasons */}
      {w.reasons.length > 0 && (
        <div className="forecast-reasons">
          <span className="section-kicker">TOP CONTRIBUTING FACTORS</span>
          <ul>
            {w.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Duration */}
      {w.duration_low_hours !== null && w.duration_high_hours !== null && (
        <div className="forecast-rows">
          <div>
            <span>Estimated outage duration</span>
            <strong>
              {w.duration_low_hours}–{w.duration_high_hours}h if one occurs
            </strong>
          </div>
          {forecast.max_gust_mph !== null && (
            <div>
              <span>Peak wind gust</span>
              <strong>{Math.round(forecast.max_gust_mph)} mph</strong>
            </div>
          )}
          {forecast.max_temp_f !== null && (
            <div>
              <span>High temperature</span>
              <strong>{Math.round(forecast.max_temp_f)}°F</strong>
            </div>
          )}
        </div>
      )}

      {/* Transfer badge */}
      {forecast.status === "transfer_estimate" && (
        <div className="forecast-transfer-notice">
          <CloudMonitoring size={16} />
          <span>
            <strong>Transfer estimate</strong> — no verified local outage history.
            Probability transferred from counties with similar physical and weather conditions.
          </span>
        </div>
      )}

      {/* Freshness */}
      <div className="forecast-meta">
        <span>
          {experimental ? "Model estimate generated" : "Forecast issued"} {age}
          {isStale && <> · <span className="forecast-stale">Stale — last complete batch</span></>}
        </span>
        <span>Model v{forecast.model_version}</span>
      </div>

      {/* County-level disclaimer (always shown) */}
      <p className="forecast-disclaimer">
        {experimental ? experimental.limitations : <>County-level estimate. This is the chance of at least one new unplanned outage
        anywhere in this county, not the chance that your home loses power.
        Planned outages and PSPS are shown separately below.</>}
      </p>
    </Tile>
  );
}

// -------------------------------------------------------------------------
// Loading skeleton
// -------------------------------------------------------------------------

function ForecastSkeleton() {
  return (
    <Tile className="forecast-card forecast-card--loading" aria-busy="true" aria-label="Loading forecast">
      <Loading small withOverlay={false} description="Loading forecast…" />
      <SkeletonText paragraph lineCount={4} />
    </Tile>
  );
}

// -------------------------------------------------------------------------
// Error state
// -------------------------------------------------------------------------

function ForecastError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Tile className="forecast-card">
      <InlineNotification
        kind="error"
        title="Forecast unavailable"
        subtitle={message}
        lowContrast
      />
      <button type="button" className="forecast-retry-btn" style={{ marginTop: 12 }} onClick={onRetry}>
        Try again
      </button>
    </Tile>
  );
}

// -------------------------------------------------------------------------
// Main export
// -------------------------------------------------------------------------

interface ForecastPanelProps {
  fips: string;
  onFipsChange: (fips: string) => void;
  flyToCoords?: { lat: number; lon: number } | null;
  /** Overlay rendered inside the map canvas (search bar, location picker). */
  mapOverlay?: React.ReactNode;
  /**
   * When provided (logged-in users), rendered in the right column beside the map.
   * The forecast detail (tabs + score card) is then shown below the map row.
   */
  rightSlot?: React.ReactNode;
}

export default function ForecastPanel({ fips, onFipsChange, flyToCoords, mapOverlay, rightSlot }: ForecastPanelProps) {
  const { state, refetch } = useForecast(fips);
  const [activeWindow, setActiveWindow] =
    useState<typeof WINDOW_KEYS[number]>("next_24h");

  const isLoading = state.phase === "loading" || state.phase === "idle";
  const isStale = state.phase === "stale";
  const forecast =
    state.phase === "success" || state.phase === "stale" ? state.data : null;

  const forecastDetail = (
    <>
      <div className="forecast-heading">
        <div>
          <span className="section-kicker">COUNTY FORECAST</span>
          <h1 id="forecast-title">Your power forecast</h1>
          <p>
            Experimental county customer impact across three UTC days.
            Live weather and canopy are separate contributing-factor layers.
          </p>
        </div>
      </div>

      {/* Window tabs */}
      {forecast && (
        <WindowTabs
          forecast={forecast}
          activeKey={activeWindow}
          onSelect={setActiveWindow}
        />
      )}

      {/* Main card */}
      {isLoading && <ForecastSkeleton />}
      {state.phase === "error" && (
        <ForecastError message={state.message} onRetry={refetch} />
      )}
      {forecast && (
        <ForecastCard
          forecast={forecast}
          windowKey={activeWindow}
          isStale={isStale}
        />
      )}

      {/* How it works accordion */}
      <details className="model-flow" style={{ padding: "0 24px" }}>
        <summary>How this forecast becomes your plan</summary>
        <div>
          <p>
            <strong>Historical inputs</strong> — 2018 EAGLE-I county customer-outage records, county geometry and calendar dates. Within-county training locations are simulated.
          </p>
          <ArrowRight size={18} aria-hidden="true" />
          <p>
            <strong>Experimental estimate</strong> — a fitted gradient-boosted model aggregates representative point estimates into typical county customer impact. It does not estimate outage probability or duration. Weather and canopy are separate layers.
          </p>
          <ArrowRight size={18} aria-hidden="true" />
          <p>
            <strong>Your plan</strong> — use your saved household inventory to prepare. Review current weather and official outage notices alongside this experimental baseline.
          </p>
        </div>
      </details>
    </>
  );

  return (
    <section className="forecast-section" aria-labelledby="forecast-title">
      {/* Stale banner — full width above the two-column grid */}
      {isStale && (
        <InlineNotification
          kind="warning"
          title="Showing last available forecast"
          subtitle="The current batch could not be loaded. Showing the previous complete batch."
          lowContrast
        />
      )}

      {/* Row 1: map (left) + plan or forecast detail (right) */}
      <div className="forecast-layout">
        {/* LEFT: map */}
        <div className="forecast-map-col">
          <CountyRiskMap
            activeFips={fips}
            activeWindow={activeWindow}
            onSelectFips={onFipsChange}
            activeForecast={forecast}
            flyToCoords={flyToCoords}
            mapOverlay={mapOverlay}
          />
        </div>

        {/* RIGHT: plan slot (logged-in) or forecast detail (logged-out) */}
        <div className="forecast-detail-col">
          {rightSlot ?? forecastDetail}
        </div>
      </div>

      {/* Row 2: forecast detail — only shown when plan occupies the right slot */}
      {rightSlot && (
        <div className="forecast-below-row">
          {forecastDetail}
        </div>
      )}
    </section>
  );
}
