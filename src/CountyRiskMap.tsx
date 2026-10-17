/**
 * CountyRiskMap — Interactive Leaflet map with OpenStreetMap tiles.
 *
 * Shows a circle marker for each covered county, colored and sized by
 * outage probability for the active forecast window.
 * Clicking a marker switches the main forecast panel to that county.
 */
import { useEffect, useRef, useState } from "react";
import { MapContainer, TileLayer, CircleMarker, Tooltip, useMap } from "react-leaflet";
import type { Map as LeafletMap } from "leaflet";
import "leaflet/dist/leaflet.css";
import type { CountyForecast } from "./types";
import { buildDemoForecast } from "./api";

// ---------------------------------------------------------------------------
// County metadata
// ---------------------------------------------------------------------------

const COUNTY_META: Record<string, { name: string; state: string; lat: number; lon: number }> = {
  "06037": { name: "Los Angeles",    state: "CA", lat: 34.05, lon: -118.24 },
  "06059": { name: "Orange",         state: "CA", lat: 33.72, lon: -117.83 },
  "06073": { name: "San Diego",      state: "CA", lat: 32.72, lon: -117.15 },
  "06065": { name: "Riverside",      state: "CA", lat: 33.95, lon: -117.40 },
  "06071": { name: "San Bernardino", state: "CA", lat: 34.04, lon: -117.29 },
  "06085": { name: "Santa Clara",    state: "CA", lat: 37.35, lon: -121.96 },
  "36061": { name: "New York",       state: "NY", lat: 40.71, lon: -74.01  },
  "48201": { name: "Harris",         state: "TX", lat: 29.76, lon: -95.37  },
  "17031": { name: "Cook",           state: "IL", lat: 41.88, lon: -87.63  },
  "53033": { name: "King",           state: "WA", lat: 47.61, lon: -122.33 },
  "12086": { name: "Miami-Dade",     state: "FL", lat: 25.77, lon: -80.19  },
  "04013": { name: "Maricopa",       state: "AZ", lat: 33.45, lon: -112.07 },
};

const FIPS_LIST = Object.keys(COUNTY_META);

// ---------------------------------------------------------------------------
// Color + radius scale
// ---------------------------------------------------------------------------

function probColor(p: number | null): string {
  if (p === null) return "#a8a8a8";
  if (p < 0.35)   return "#24a148"; // green
  if (p < 0.60)   return "#f1c21b"; // amber
  if (p < 0.80)   return "#ff832b"; // orange
  return "#da1e28";                  // red
}

function probRadius(p: number | null, isActive: boolean): number {
  const base = p === null ? 10 : 10 + Math.round(p * 14);
  return isActive ? base + 5 : base;
}

function probLabel(p: number | null): string {
  return p === null ? "—" : `${Math.round(p * 100)}%`;
}

// ---------------------------------------------------------------------------
// Sub-component: fly to active county when fips changes
// ---------------------------------------------------------------------------

function FlyTo({ fips }: { fips: string }) {
  const map = useMap();
  const mounted = useRef(false);
  useEffect(() => {
    // Skip the first render — let the MapContainer center prop handle initial position.
    // Only fly when the user actively changes county.
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    const meta = COUNTY_META[fips];
    if (meta) {
      map.flyTo([meta.lat, meta.lon], Math.max(map.getZoom(), 7), { duration: 0.8 });
    }
  }, [fips, map]);
  return null;
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WindowKey = "next_24h" | "next_24_48h" | "next_48_72h";

interface Props {
  activeFips: string;
  activeWindow: WindowKey;
  onSelectFips: (fips: string) => void;
  activeForecast: CountyForecast | null;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function CountyRiskMap({ activeFips, activeWindow, onSelectFips, activeForecast }: Props) {
  const [forecasts, setForecasts] = useState<Record<string, CountyForecast>>({});
  const mapRef = useRef<LeafletMap | null>(null);

  // Load all county forecasts on mount
  useEffect(() => {
    let cancelled = false;
    Promise.all(
      FIPS_LIST.map(async (fips) => {
        try {
          const { fetchForecast } = await import("./api");
          const result = await fetchForecast(fips);
          if (result.phase === "success" || result.phase === "stale") {
            return [fips, result.data] as const;
          }
        } catch { /* ignore */ }
        return [fips, buildDemoForecast(fips)] as const;
      })
    ).then((entries) => {
      if (!cancelled) setForecasts(Object.fromEntries(entries));
    });
    return () => { cancelled = true; };
  }, []);

  // Keep active county in sync with parent
  useEffect(() => {
    if (activeForecast) {
      setForecasts((prev) => ({ ...prev, [activeFips]: activeForecast }));
    }
  }, [activeFips, activeForecast]);

  const windowLabel =
    activeWindow === "next_24h" ? "NEXT 24H" :
    activeWindow === "next_24_48h" ? "24–48H" : "48–72H";

  return (
    <div className="risk-map-wrap">
      <div className="risk-map-header">
        <span className="section-kicker">COUNTY RISK MAP · {windowLabel}</span>
        <p>Outage probability by county — click any marker to switch the forecast view.</p>
      </div>

      <div className="risk-map-canvas">
        <MapContainer
          center={[38.5, -98]}
          zoom={4}
          scrollWheelZoom={true}
          style={{ height: "420px", width: "100%" }}
          ref={mapRef}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          <FlyTo fips={activeFips} />

          {FIPS_LIST.map((fips) => {
            const meta = COUNTY_META[fips];
            const fc = forecasts[fips];
            const prob = fc ? (fc[activeWindow]?.probability ?? null) : null;
            const isActive = fips === activeFips;
            const color = probColor(prob);
            const radius = probRadius(prob, isActive);

            return (
              <CircleMarker
                key={fips}
                center={[meta.lat, meta.lon]}
                radius={radius}
                pathOptions={{
                  fillColor: color,
                  fillOpacity: isActive ? 1.0 : 0.82,
                  color: isActive ? "#161616" : "#fff",
                  weight: isActive ? 3 : 1.5,
                }}
                eventHandlers={{
                  click: () => onSelectFips(fips),
                }}
              >
                <Tooltip
                  permanent={false}
                  direction="top"
                  offset={[0, -radius]}
                  opacity={0.95}
                >
                  <div className="risk-map-tt">
                    <strong>{meta.name}, {meta.state}</strong>
                    <span style={{ color, fontWeight: 700 }}>{probLabel(prob)}</span>
                    {fc?.next_24h.reasons[0] && (
                      <em>{fc[activeWindow].reasons[0]}</em>
                    )}
                  </div>
                </Tooltip>
              </CircleMarker>
            );
          })}
        </MapContainer>

        {/* Legend */}
        <div className="risk-map-legend">
          <span className="section-kicker">RISK</span>
          {[
            { label: "Low < 35%",    color: "#24a148" },
            { label: "Mod 35–60%",   color: "#f1c21b" },
            { label: "High 60–80%",  color: "#ff832b" },
            { label: "Severe > 80%", color: "#da1e28" },
          ].map(({ label, color }) => (
            <span key={label} className="risk-legend-item">
              <span className="risk-legend-dot" style={{ background: color }} />
              {label}
            </span>
          ))}
          <span className="risk-legend-note">Larger circle = higher risk</span>
        </div>
      </div>
    </div>
  );
}
