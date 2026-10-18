/**
 * County Risk Map
 *
 * Visualisation strategy:
 *  1. Heatmap layer  – smooth spatial gradient from the point source; gives an
 *     intuitive "hot zone" feel even where county boundaries aren't shown.
 *  2. County-fill layer – Mapbox's built-in `admin-2-fills` tileset colored by
 *     the county's outage probability, giving polygon-level spatial context.
 *  3. County-outline layer – thin border so individual counties are legible.
 *  4. Label / click layer – small circle + percentage label, still clickable.
 */
import { useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import type { FeatureCollection, Point } from "geojson";
import "mapbox-gl/dist/mapbox-gl.css";
import type { CountyForecast } from "./types";
import { buildDemoForecast, fetchForecast } from "./api";

const COUNTIES: Record<string, { name: string; lat: number; lon: number }> = {
  "06037": { name: "Los Angeles",    lat: 34.052,  lon: -118.244 },
  "06059": { name: "Orange",         lat: 33.717,  lon: -117.831 },
  "06073": { name: "San Diego",      lat: 32.716,  lon: -117.163 },
  "06065": { name: "Riverside",      lat: 33.82,   lon: -116.55  },
  "06071": { name: "San Bernardino", lat: 34.65,   lon: -116.65  },
  "06085": { name: "Santa Clara",    lat: 37.335,  lon: -121.893 },
  "36061": { name: "New York",       lat: 40.713,  lon:  -74.006 },
  "48201": { name: "Harris",         lat: 29.763,  lon:  -95.363 },
  "17031": { name: "Cook",           lat: 41.883,  lon:  -87.632 },
  "53033": { name: "King",           lat: 47.601,  lon: -122.332 },
  "12086": { name: "Miami-Dade",     lat: 25.774,  lon:  -80.198 },
  "04013": { name: "Maricopa",       lat: 33.448,  lon: -112.074 },
};
const FIPS_LIST = Object.keys(COUNTIES);
const SOURCE    = "county-risk";

// Carbon Design System's Gray 10 Mapbox style.
const CARBON_G10_STYLE = "mapbox://styles/carbondesignsystem/ck7c8ce1y05h61ipb2fixfe76";

type WindowKey = "next_24h" | "next_24_48h" | "next_48_72h";

// ── colour helpers ──────────────────────────────────────────────────────────

/** Returns a CSS hex for a 0-1 probability (used for the legend & fill layer). */
function probColor(p: number | null): string {
  if (p === null) return "#8d8d8d";
  if (p < 0.35)   return "#198038"; // low  – green
  if (p < 0.60)   return "#b28600"; // mod  – amber
  if (p < 0.80)   return "#ff832b"; // high – orange
  return "#da1e28";                  // severe – red
}

// ── GeoJSON builders ────────────────────────────────────────────────────────

/** Point source used by the heatmap and label layers. */
function pointsGeoJSON(
  forecasts: Record<string, CountyForecast>,
  activeFips: string,
  window: WindowKey,
): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: FIPS_LIST.map((fips) => {
      const county = COUNTIES[fips];
      const p      = forecasts[fips]?.[window]?.probability ?? null;
      return {
        type: "Feature",
        id: fips,
        geometry: { type: "Point", coordinates: [county.lon, county.lat] },
        properties: {
          fips,
          name:   county.name,
          value:  p === null ? "—" : `${Math.round(p * 100)}%`,
          color:  probColor(p),
          prob:   p ?? 0,
          prob2:  (p ?? 0) ** 2,     // squared weight: centres dominate, low-risk barely registers
          active: fips === activeFips,
        },
      };
    }),
  };
}

// ── Props ───────────────────────────────────────────────────────────────────

interface Props {
  activeFips:     string;
  activeWindow:   WindowKey;
  onSelectFips:   (fips: string) => void;
  activeForecast: CountyForecast | null;
}

// ── Component ───────────────────────────────────────────────────────────────

export default function CountyRiskMap({
  activeFips, activeWindow, onSelectFips, activeForecast,
}: Props) {
  const containerRef  = useRef<HTMLDivElement>(null);
  const mapRef        = useRef<mapboxgl.Map | null>(null);
  const latest        = useRef({ activeFips, activeWindow, onSelectFips, forecasts: {} as Record<string, CountyForecast> });
  const previousFips  = useRef(activeFips);
  // Seed with demo forecasts immediately so the heatmap has data on first render.
  const [forecasts, setForecasts] = useState<Record<string, CountyForecast>>(
    () => Object.fromEntries(FIPS_LIST.map((fips) => [fips, buildDemoForecast(fips)]))
  );
  const accessToken   = (import.meta.env.VITE_MAPBOX_ACCESS_TOKEN as string | undefined) ?? "";
  latest.current      = { activeFips, activeWindow, onSelectFips, forecasts };

  // Fetch forecasts for all counties on mount.
  useEffect(() => {
    let cancelled = false;
    Promise.all(FIPS_LIST.map(async (fips) => {
      try {
        const result = await fetchForecast(fips);
        if (result.phase === "success" || result.phase === "stale") return [fips, result.data] as const;
      } catch { /* fall through */ }
      return [fips, buildDemoForecast(fips)] as const;
    })).then((entries) => {
      if (!cancelled) setForecasts(Object.fromEntries(entries));
    });
    return () => { cancelled = true; };
  }, []);

  // Merge freshly-loaded active-county forecast.
  useEffect(() => {
    if (activeForecast) setForecasts((prev) => ({ ...prev, [activeFips]: activeForecast }));
  }, [activeFips, activeForecast]);

  // ── Map initialisation ──────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || mapRef.current || !accessToken) return;
    const initial = COUNTIES[latest.current.activeFips] ?? COUNTIES["06037"];
    const map = new mapboxgl.Map({
      accessToken,
      container:          containerRef.current,
      style:              CARBON_G10_STYLE,
      center:             initial === COUNTIES["06037"] ? [-117.6, 33.9] : [initial.lon, initial.lat],
      zoom:               6.6,
      attributionControl: false,
    });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new mapboxgl.AttributionControl({ compact: true }),     "bottom-right");
    mapRef.current = map;

    map.once("load", () => {
      const cur = latest.current;
      const pts = pointsGeoJSON(cur.forecasts, cur.activeFips, cur.activeWindow);

      // ── Point source (heatmap + labels) ───────────────────────────────────
      map.addSource(SOURCE, { type: "geojson", data: pts });

      // ── Layer: heatmap (spatial blur) ──────────────────────────────────────
      map.addLayer({
        id:     "county-risk-heat",
        type:   "heatmap",
        source: SOURCE,
        maxzoom: 15,
        paint:  {
          // Use pre-computed prob² so high-risk centres dominate the Gaussian
          // while low-risk points barely register (0.9² = 0.81, 0.3² = 0.09).
          "heatmap-weight": ["get", "prob2"],
          // Boost intensity with zoom so the glow stays vivid when zoomed in.
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 7, 1.5, 12, 3.0],
          // Thermal palette: blue → cyan → yellow-green → yellow → orange → red.
          "heatmap-color": [
            "interpolate", ["linear"], ["heatmap-density"],
            0,    "rgba(0,0,255,0)",
            0.10, "rgba(0,20,220,0.72)",
            0.25, "rgba(0,180,200,0.80)",
            0.40, "rgba(80,220,60,0.82)",
            0.55, "rgba(230,230,0,0.85)",
            0.70, "rgba(255,140,0,0.88)",
            0.85, "rgba(220,30,30,0.92)",
            1,    "rgba(160,0,0,0.96)",
          ],
          // Wider radius — roughly 1.5× the previous values so each county
          // casts a larger Gaussian footprint and adjacent counties blend.
          "heatmap-radius":  ["interpolate", ["linear"], ["zoom"], 3, 80, 6, 180, 9, 300, 12, 480],
          // Stay fully visible at all zoom levels — only dissolve past 14.
          "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0.90, 14, 0.88, 15, 0],
        },
      });

      // ── Layer: active-county selection halo ───────────────────────────────
      map.addLayer({
        id: "county-risk-halo", type: "circle", source: SOURCE,
        paint: {
          "circle-radius":  22,
          "circle-color":   "#161616",
          "circle-opacity": ["case", ["get", "active"], 0.85, 0],
        },
      });

      // ── Layer: small dot markers ───────────────────────────────────────────
      map.addLayer({
        id: "county-risk-dots", type: "circle", source: SOURCE,
        paint: {
          "circle-radius":        14,
          "circle-color":         ["get", "color"],
          "circle-stroke-color":  "#ffffff",
          "circle-stroke-width":  2,
          "circle-opacity":       ["interpolate", ["linear"], ["zoom"], 7, 0.9, 9.5, 1],
        },
      });

      // ── Layer: probability label ──────────────────────────────────────────
      map.addLayer({
        id: "county-risk-values", type: "symbol", source: SOURCE,
        layout: {
          "text-field":              ["get", "value"],
          "text-size":               11,
          "text-allow-overlap":      true,
          "text-ignore-placement":   true,
        },
        paint: { "text-color": "#ffffff" },
      });

      // ── Layer: county name label ──────────────────────────────────────────
      map.addLayer({
        id: "county-risk-names", type: "symbol", source: SOURCE,
        layout: {
          "text-field":            ["get", "name"],
          "text-size":             11,
          "text-offset":           [0, 2.5],
          "text-anchor":           "top",
          "text-allow-overlap":    true,
          "text-ignore-placement": true,
        },
        paint: {
          "text-color":       "#161616",
          "text-halo-color":  "#ffffff",
          "text-halo-width":  2,
        },
      });

      // ── Click handler ─────────────────────────────────────────────────────
      const CLICK_LAYERS = ["county-risk-dots", "county-risk-values", "county-risk-names"];
      map.on("click", (event) => {
        const hit  = map.queryRenderedFeatures(event.point, { layers: CLICK_LAYERS })[0];
        const fips = hit?.properties?.fips as string | undefined;
        if (fips && COUNTIES[fips]) latest.current.onSelectFips(fips);
      });
      CLICK_LAYERS.forEach((layer) => {
        map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
      });
    });

    return () => { map.remove(); mapRef.current = null; };
  }, [accessToken]);

  // ── Sync data when forecasts / window / selection change ─────────────────
  useEffect(() => {
    const src = mapRef.current?.getSource(SOURCE) as mapboxgl.GeoJSONSource | undefined;
    src?.setData(pointsGeoJSON(forecasts, activeFips, activeWindow));
  }, [forecasts, activeFips, activeWindow]);

  // ── Fly to selected county ────────────────────────────────────────────────
  useEffect(() => {
    if (previousFips.current === activeFips) return;
    previousFips.current = activeFips;
    const county = COUNTIES[activeFips];
    const map    = mapRef.current;
    if (county && map) {
      map.flyTo({ center: [county.lon, county.lat], zoom: Math.max(map.getZoom(), 6.6), duration: 700 });
    }
  }, [activeFips]);

  const windowLabel = activeWindow === "next_24h" ? "NEXT 24H"
                    : activeWindow === "next_24_48h" ? "24–48H" : "48–72H";

  return (
    <div className="risk-map-wrap">
      <div className="risk-map-header">
        <span className="section-kicker">COUNTY RISK MAP · {windowLabel}</span>
        <p>Outage probability heat map — warm zones indicate higher risk. Click any county to load its forecast.</p>
      </div>
      <div className="risk-map-canvas">
        {!accessToken && (
          <div className="risk-map-key-notice">
            Add a Mapbox public token as <code>VITE_MAPBOX_ACCESS_TOKEN</code> in <code>.env.development.local</code> to show the risk heat map.
          </div>
        )}
        <div ref={containerRef} style={{ height: "440px", width: "100%" }} />
        <div className="risk-map-legend">
          <span className="section-kicker">RISK</span>
          {([
            { label: "Low",    color: "rgba(0,180,200,0.80)"  },
            { label: "Mod",    color: "rgba(230,230,0,0.85)"  },
            { label: "High",   color: "rgba(255,140,0,0.88)"  },
            { label: "Severe", color: "rgba(220,30,30,0.92)"  },
          ] as const).map(({ label, color }) => (
            <span key={label} className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: color }} />
              {label}
            </span>
          ))}
          <span className="risk-legend-note">Thermal heatmap · Click to select</span>
        </div>
      </div>
    </div>
  );
}

