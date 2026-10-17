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
import CountySelector from "./CountySelector";
import { useForecast } from "./useForecast";
import { formatForecastAge, formatProbability, statusBadgeText } from "./api";
import type { CountyForecast, ForecastWindow } from "./types";

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
        const pct = prob !== null ? `${Math.round(prob * 100)}%` : "—";
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={key === activeKey}
            className={key === activeKey ? "selected" : ""}
            onClick={() => onSelect(key)}
          >
            <span>{w.window_label}</span>
            <strong>{pct}</strong>
            <span>
              {prob !== null
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
      {prob !== null ? (
        <>
          <h2 id="forecast-card-title" className="forecast-prob">{probPct}</h2>
          <p className="forecast-prob-label">estimated chance of a new unplanned outage in this county</p>
        </>
      ) : (
        <>
          <h2 id="forecast-card-title" className="forecast-no-data">—</h2>
          <p className="forecast-no-data-label">
            {forecast.status === "insufficient_data"
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
          Forecast issued {age}
          {isStale && <> · <span className="forecast-stale">Stale — last complete batch</span></>}
        </span>
        <span>Model v{forecast.model_version}</span>
      </div>

      {/* County-level disclaimer (always shown) */}
      <p className="forecast-disclaimer">
        County-level estimate. This is the chance of at least one new unplanned outage
        anywhere in this county, not the chance that your home loses power.
        Planned outages and PSPS are shown separately below.
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
}

export default function ForecastPanel({ fips, onFipsChange }: ForecastPanelProps) {
  const { state, refetch } = useForecast(fips);
  const [activeWindow, setActiveWindow] =
    useState<typeof WINDOW_KEYS[number]>("next_24h");

  const isLoading = state.phase === "loading" || state.phase === "idle";
  const isStale = state.phase === "stale";
  const forecast =
    state.phase === "success" || state.phase === "stale" ? state.data : null;

  return (
    <section className="forecast-section" aria-labelledby="forecast-title">
      <div className="forecast-heading">
        <div>
          <span className="section-kicker">COUNTY FORECAST</span>
          <h1 id="forecast-title">Your power forecast</h1>
          <p>
            Estimated chance of a new unplanned outage anywhere in the selected
            county, for each 24-hour window.
          </p>
        </div>
        <CountySelector value={fips} onChange={onFipsChange} />
      </div>

      {/* Stale banner */}
      {isStale && (
        <InlineNotification
          kind="warning"
          title="Showing last available forecast"
          subtitle="The current batch could not be loaded. Showing the previous complete batch."
          lowContrast
        />
      )}

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
      <details className="model-flow">
        <summary>How this forecast becomes your plan</summary>
        <div>
          <p>
            <strong>Verified inputs</strong> — NOAA GFS weather forecasts, NWS
            alerts, USGS canopy and terrain, and verified unplanned outage history
            (planned and PSPS excluded).
          </p>
          <ArrowRight size={18} aria-hidden="true" />
          <p>
            <strong>Prediction model</strong> — county-level gradient-boosted
            onset model, calibrated per window. Counties without local history
            receive a transferred estimate or an insufficient-data state.
          </p>
          <ArrowRight size={18} aria-hidden="true" />
          <p>
            <strong>Your plan</strong> — forecast conditions combined with your
            household answers. Planned outages and PSPS notices appear separately
            and never enter the model.
          </p>
        </div>
      </details>
    </section>
  );
}
