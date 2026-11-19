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
import type { FeatureCollection, GeoJsonProperties, Geometry, Point } from "geojson";
import "mapbox-gl/dist/mapbox-gl.css";
import type { CountyForecast } from "./types";
import { buildDemoForecast, fetchForecast } from "./api";
import { fetchMapMetadata, installCanopy, installWeather, showMapLayer, type CanopyMap, type WeatherMap } from "./mapRasters";

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
  /** When set (and non-null), fly the map to these coordinates at zoom 11. */
  flyToCoords?:   { lat: number; lon: number } | null;
  /** Rendered as an absolutely-positioned overlay inside the map canvas (top-left). */
  mapOverlay?:    React.ReactNode;
}

// ── Component ───────────────────────────────────────────────────────────────

export default function CountyRiskMap({
  activeFips, activeWindow, onSelectFips, activeForecast, flyToCoords, mapOverlay,
}: Props) {
  const containerRef  = useRef<HTMLDivElement>(null);
  const mapRef        = useRef<mapboxgl.Map | null>(null);
  const latest        = useRef({ activeFips, activeWindow, onSelectFips, forecasts: {} as Record<string, CountyForecast> });
  const previousFips  = useRef(activeFips);
  // Seed with demo forecasts immediately so the heatmap has data on first render.
  const [forecasts, setForecasts] = useState<Record<string, CountyForecast>>(
    () => Object.fromEntries(FIPS_LIST.map((fips) => [fips, buildDemoForecast(fips)]))
  );
  const [layerPanelOpen,   setLayerPanelOpen]   = useState(true);
  const [showRiskLayer,    setShowRiskLayer]    = useState(true);
  const [showEagleiLayer,  setShowEagleiLayer]  = useState(false);
  const [showCecLayer,     setShowCecLayer]     = useState(false);
  const [showCanopyLayer,  setShowCanopyLayer]  = useState(false);
  const [showWeatherLayer, setShowWeatherLayer] = useState(false);
  const [canopyMap, setCanopyMap] = useState<CanopyMap | null>(null);
  const [weatherMap, setWeatherMap] = useState<WeatherMap | null>(null);
  const [weatherRefreshFailed, setWeatherRefreshFailed] = useState(false);
  const rasterVisibility = useRef({ canopy: showCanopyLayer, weather: showWeatherLayer });
  rasterVisibility.current = { canopy: showCanopyLayer, weather: showWeatherLayer };
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

    let disposed = false;
    let rasterTimer: ReturnType<typeof setInterval> | undefined;
    map.once("load", () => {
      let canopyVersion = "";
      let weatherVersion = "";
      const refreshRasters = async () => {
        await Promise.allSettled([
          fetchMapMetadata<CanopyMap>("canopy/latest.json", "canopy-map/v1").then((data) => {
            if (disposed) return;
            const version = `${data.tiles}:${data.minzoom}:${data.maxzoom}`;
            if (version !== canopyVersion) {
              installCanopy(map, data, rasterVisibility.current.canopy);
              canopyVersion = version;
            }
            setCanopyMap(data);
          }),
          fetchMapMetadata<WeatherMap>("weather/latest.json", "weather-map/v1").then((data) => {
            if (disposed) return;
            if (data.batch_id !== weatherVersion) {
              installWeather(map, data, latest.current.activeWindow, rasterVisibility.current.weather);
              weatherVersion = data.batch_id;
            }
            setWeatherMap(data);
            setWeatherRefreshFailed(false);
          }).catch(() => { if (!disposed) setWeatherRefreshFailed(true); }),
        ]);
      };
      void refreshRasters();
      rasterTimer = setInterval(() => { void refreshRasters(); }, 5 * 60_000);
      const cur = latest.current;
      const pts = pointsGeoJSON(cur.forecasts, cur.activeFips, cur.activeWindow);

      // ── County boundaries source (shared by EAGLE-I, canopy + future layers) ──
      // Fetch the county boundary GeoJSON, then fetch impact/canopy/weather
      // values and join them as properties before setting the source data.
      fetch("/data/county-boundaries.geojson")
        .then((r) => r.json())
        .then((boundaryFC: FeatureCollection<Geometry, GeoJsonProperties>) => {
          // Add source immediately with boundary data (data join follows).
          map.addSource("county-boundaries", { type: "geojson", data: boundaryFC });

          // ── eaglei-impact-fill layer (visibility: none until toggled) ────
          map.addLayer({
            id:     "eaglei-impact-fill",
            type:   "fill",
            source: "county-boundaries",
            layout: { visibility: "none" },
            paint:  {
              "fill-color": [
                "interpolate", ["linear"], ["coalesce", ["get", "impact"], 0],
                0,    "rgba(219,234,254,0)",
                200,  "rgba(147,197,253,0.5)",
                1000, "rgba(37,99,235,0.65)",
                5000, "rgba(30,58,138,0.8)",
              ],
              "fill-outline-color": "rgba(0,0,0,0)",
            },
          }, "county-risk-heat");

          // Historical impact remains county-level; canopy/weather use rasters.
          fetch("/data/eaglei_impact.json")
            .then((r) => r.json() as Promise<{ counties: Record<string, number> }>)
            .then((data) => {
              if (disposed) return;
              const joined = {
                ...boundaryFC,
                features: boundaryFC.features.map((f) => ({
                  ...f,
                  properties: { ...f.properties, impact: data.counties[f.properties?.GEOID as string] ?? null },
                })),
              };
              (map.getSource("county-boundaries") as mapboxgl.GeoJSONSource).setData(joined);
            })
            .catch((err) => console.warn("Historical impact data unavailable", err));
        })
        .catch((err) => console.warn("county-boundaries.geojson fetch failed:", err));

      // ── CEC transmission lines source + layer ────────────────────────────
      map.addSource("cec-transmission", {
        type: "geojson",
        data: "/data/cec-transmission-lines.geojson",
      });
      map.addLayer({
        id:     "cec-lines",
        type:   "line",
        source: "cec-transmission",
        layout: { visibility: "none" },
        paint:  {
          "line-color": [
            "match", ["get", "Type"],
            "OH", "#f1c21b",
            "#8d8d8d",
          ],
          "line-width":   1.5,
          "line-opacity": 0.8,
        },
      });

      // ── Point source (heatmap + labels) ───────────────────────────────────
      map.addSource(SOURCE, { type: "geojson", data: pts });

      // ── Layer: heatmap (spatial blur) ──────────────────────────────────────
      map.addLayer({
        id:     "county-risk-heat",
        type:   "heatmap",
        source: SOURCE,
        layout: { visibility: "visible" },
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

    return () => {
      disposed = true;
      if (rasterTimer) clearInterval(rasterTimer);
      map.remove(); mapRef.current = null;
    };
  }, [accessToken]);

  // ── Sync data when forecasts / window / selection change ─────────────────
  useEffect(() => {
    const src = mapRef.current?.getSource(SOURCE) as mapboxgl.GeoJSONSource | undefined;
    src?.setData(pointsGeoJSON(forecasts, activeFips, activeWindow));
  }, [forecasts, activeFips, activeWindow]);

  useEffect(() => {
    const map = mapRef.current;
    if (map && weatherMap && map.getSource("weather-risk-raster")) {
      installWeather(map, weatherMap, activeWindow, showWeatherLayer);
    }
  }, [activeWindow, weatherMap, showWeatherLayer]);

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

  // ── Fly to address-search result ──────────────────────────────────────────
  useEffect(() => {
    if (!flyToCoords) return;
    const map = mapRef.current;
    if (map) {
      map.flyTo({ center: [flyToCoords.lon, flyToCoords.lat], zoom: 11, duration: 700 });
    }
  }, [flyToCoords]);

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
        {mapOverlay && (
          <div className="risk-map-overlay">{mapOverlay}</div>
        )}
        <div className={`map-layer-panel${layerPanelOpen ? "" : " map-layer-panel--collapsed"}`}>
          <button
            type="button"
            className="layer-panel-header"
            onClick={() => setLayerPanelOpen((v) => !v)}
            aria-expanded={layerPanelOpen}
          >
            <span className="section-kicker">MAP LAYERS</span>
            <span className="layer-panel-chevron" aria-hidden="true">
              {layerPanelOpen ? "▾" : "▸"}
            </span>
          </button>
          <div className="layer-panel-body">
          <label className="layer-panel-row">
            <input
              type="checkbox"
              checked={showRiskLayer}
              onChange={(e) => {
                const on = e.target.checked;
                setShowRiskLayer(on);
                const map = mapRef.current;
                if (!map) return;
                const v = on ? "visible" : "none";
                map.setLayoutProperty("county-risk-heat",   "visibility", v);
                map.setLayoutProperty("county-risk-halo",   "visibility", v);
                map.setLayoutProperty("county-risk-dots",   "visibility", v);
                map.setLayoutProperty("county-risk-values", "visibility", v);
                map.setLayoutProperty("county-risk-names",  "visibility", v);
              }}
            />
            Outage risk heatmap
          </label>
          <label className="layer-panel-row">
            <input
              type="checkbox"
              checked={showEagleiLayer}
              onChange={(e) => {
                const on = e.target.checked;
                setShowEagleiLayer(on);
                mapRef.current?.setLayoutProperty(
                  "eaglei-impact-fill", "visibility", on ? "visible" : "none",
                );
              }}
            />
            EAGLE-I 2018 impact
          </label>
          <label className="layer-panel-row">
            <input
              type="checkbox"
              checked={showCecLayer}
              onChange={(e) => {
                const on = e.target.checked;
                setShowCecLayer(on);
                mapRef.current?.setLayoutProperty(
                  "cec-lines", "visibility", on ? "visible" : "none",
                );
              }}
            />
            CA transmission lines
          </label>
          <label className="layer-panel-row">
            <input
              type="checkbox"
              disabled={!canopyMap}
              checked={showCanopyLayer}
              onChange={(e) => {
                const on = e.target.checked;
                setShowCanopyLayer(on);
                showMapLayer(mapRef.current, "canopy-raster", on);
              }}
            />
            Tree canopy (30 m raster){!canopyMap ? " · preparing" : ""}
          </label>
          <label className="layer-panel-row">
            <input
              type="checkbox"
              disabled={!weatherMap}
              checked={showWeatherLayer}
              onChange={(e) => {
                const on = e.target.checked;
                setShowWeatherLayer(on);
                showMapLayer(mapRef.current, "weather-risk-raster", on);
              }}
            />
            DWD weather risk (live raster){!weatherMap ? " · awaiting first batch" : ""}
          </label>
          <p className="layer-panel-disclaimer">
            These layers show contributing factors, not outage probabilities.
          </p>
          </div>
        </div>
        {showEagleiLayer && (
          <div className="risk-map-legend eaglei-legend">
            <span className="section-kicker">EAGLE-I IMPACT</span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(219,234,254,0.6)", border: "1px solid #ccc" }} />
              Low
            </span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(147,197,253,0.65)" }} />
              Med
            </span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(30,58,138,0.8)" }} />
              High
            </span>
            <span className="risk-legend-note">
              2018 outage customer-impact (experimental allocation — not an outage probability)
            </span>
          </div>
        )}
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
        {showCanopyLayer && (
          <div className="risk-map-legend canopy-legend">
            <span className="section-kicker">TREE CANOPY</span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(255,255,255,0.6)", border: "1px solid #ccc" }} />
              0%
            </span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(100,180,80,0.5)" }} />
              50%
            </span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(20,100,20,0.75)" }} />
              100%
            </span>
            <span className="risk-legend-note raster-data-status">
              Tree canopy cover — {canopyMap?.vintage ?? "NLCD 2025 v2025-6"} · 30 m source; averaged at wider zooms
              {canopyMap && canopyMap.maxzoom < 12 && " · Native-detail tiles preparing"}
            </span>
          </div>
        )}
        {showWeatherLayer && (
          <div className="risk-map-legend weather-legend">
            <span className="section-kicker">DWD WEATHER RISK</span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(0,20,220,0.5)" }} />
              Low
            </span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(0,180,200,0.6)" }} />
              Mod
            </span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(230,230,0,0.65)" }} />
              High
            </span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "rgba(220,30,30,0.75)" }} />
              Severe
            </span>
            <span className="risk-legend-note raster-data-status">
              DWD environmental score · maximum per pixel for this window · roughly {Math.round((weatherMap?.grid_resolution_degrees ?? .125) * 111)} km weather grid
              {weatherMap && <> · Issued {new Date(weatherMap.selected_run).toLocaleString()} · Valid {new Date(weatherMap.windows[activeWindow].start).toLocaleString()}–{new Date(weatherMap.windows[activeWindow].end).toLocaleString()}</>}
              {weatherMap && Date.now() - Date.parse(weatherMap.selected_run) > 12 * 3600_000 && " · STALE FORECAST"}
              {weatherRefreshFailed && " · Refresh unavailable; showing last loaded batch"}
              {!!weatherMap?.coverage_summary?.missing_environment_cells && " · Cells with incomplete environmental data are transparent"}
              {" · Uncalibrated; not an outage probability"}
            </span>
          </div>
        )}
        {showCecLayer && (
          <div className="risk-map-legend cec-legend">
            <span className="section-kicker">CEC LINES</span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "#f1c21b" }} />
              Overhead (OH)
            </span>
            <span className="risk-legend-item">
              <span className="risk-legend-swatch" style={{ background: "#8d8d8d" }} />
              Underground / other
            </span>
            <span className="risk-legend-note">
              CA transmission lines (CEC — overhead/underground, California only)
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
