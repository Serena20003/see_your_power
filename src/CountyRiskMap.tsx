/** County forecast points live in the map's geographic coordinate system. */
import { useEffect, useRef, useState } from "react";
import mapboxgl from "mapbox-gl";
import type { FeatureCollection, Point } from "geojson";
import "mapbox-gl/dist/mapbox-gl.css";
import type { CountyForecast } from "./types";
import { buildDemoForecast, fetchForecast } from "./api";

const COUNTIES: Record<string, { name: string; lat: number; lon: number }> = {
  "06037": { name: "Los Angeles", lat: 34.052, lon: -118.244 },
  "06059": { name: "Orange", lat: 33.717, lon: -117.831 },
  "06073": { name: "San Diego", lat: 32.716, lon: -117.163 },
  "06065": { name: "Riverside", lat: 33.82, lon: -116.55 },
  "06071": { name: "San Bernardino", lat: 34.65, lon: -116.65 },
  "06085": { name: "Santa Clara", lat: 37.335, lon: -121.893 },
  "36061": { name: "New York", lat: 40.713, lon: -74.006 },
  "48201": { name: "Harris", lat: 29.763, lon: -95.363 },
  "17031": { name: "Cook", lat: 41.883, lon: -87.632 },
  "53033": { name: "King", lat: 47.601, lon: -122.332 },
  "12086": { name: "Miami-Dade", lat: 25.774, lon: -80.198 },
  "04013": { name: "Maricopa", lat: 33.448, lon: -112.074 },
};
const FIPS_LIST = Object.keys(COUNTIES);
const SOURCE = "county-risk";
const CLICK_LAYERS = ["county-risk-dots", "county-risk-values", "county-risk-names"];
// Carbon Design System's Gray 10 Mapbox style matches the app's g10 theme.
const CARBON_G10_STYLE = "mapbox://styles/carbondesignsystem/ck7c8ce1y05h61ipb2fixfe76";
type WindowKey = "next_24h" | "next_24_48h" | "next_48_72h";

function color(p: number | null): string {
  if (p === null) return "#8d8d8d";
  if (p < 0.35) return "#198038";
  if (p < 0.60) return "#b28600";
  if (p < 0.80) return "#ff832b";
  return "#da1e28";
}

function points(forecasts: Record<string, CountyForecast>, activeFips: string, window: WindowKey): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: FIPS_LIST.map((fips) => {
      const county = COUNTIES[fips];
      const p = forecasts[fips]?.[window]?.probability ?? null;
      return {
        type: "Feature",
        id: fips,
        geometry: { type: "Point", coordinates: [county.lon, county.lat] },
        properties: {
          fips, name: county.name,
          value: p === null ? "—" : `${Math.round(p * 100)}%`,
          color: color(p),
          radius: p === null ? 17 : 16 + Math.round(p * 7),
          active: fips === activeFips,
        },
      };
    }),
  };
}

interface Props {
  activeFips: string;
  activeWindow: WindowKey;
  onSelectFips: (fips: string) => void;
  activeForecast: CountyForecast | null;
}

export default function CountyRiskMap({ activeFips, activeWindow, onSelectFips, activeForecast }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const latest = useRef({ activeFips, activeWindow, onSelectFips, forecasts: {} as Record<string, CountyForecast> });
  const previousFips = useRef(activeFips);
  const [forecasts, setForecasts] = useState<Record<string, CountyForecast>>({});
  const accessToken = (import.meta.env.VITE_MAPBOX_ACCESS_TOKEN as string | undefined) || "";
  latest.current = { activeFips, activeWindow, onSelectFips, forecasts };

  useEffect(() => {
    let cancelled = false;
    Promise.all(FIPS_LIST.map(async (fips) => {
      try {
        const result = await fetchForecast(fips);
        if (result.phase === "success" || result.phase === "stale") return [fips, result.data] as const;
      } catch { /* Local demo fallback. */ }
      return [fips, buildDemoForecast(fips)] as const;
    })).then((entries) => {
      if (!cancelled) setForecasts(Object.fromEntries(entries));
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (activeForecast) setForecasts((prev) => ({ ...prev, [activeFips]: activeForecast }));
  }, [activeFips, activeForecast]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current || !accessToken) return;
    const initial = COUNTIES[latest.current.activeFips] ?? COUNTIES["06037"];
    const map = new mapboxgl.Map({
      accessToken,
      container: containerRef.current,
      style: CARBON_G10_STYLE,
      center: initial === COUNTIES["06037"] ? [-117.6, 33.9] : [initial.lon, initial.lat],
      zoom: 6.6,
      attributionControl: false,
    });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new mapboxgl.AttributionControl({ compact: true }), "bottom-right");
    mapRef.current = map;

    map.once("load", () => {
      const current = latest.current;
      map.addSource(SOURCE, { type: "geojson", data: points(current.forecasts, current.activeFips, current.activeWindow) });
      map.addLayer({
        id: "county-risk-halo", type: "circle", source: SOURCE,
        paint: {
          "circle-radius": ["+", ["get", "radius"], 5],
          "circle-color": "#161616",
          "circle-opacity": ["case", ["get", "active"], 0.9, 0],
        },
      });
      map.addLayer({
        id: "county-risk-dots", type: "circle", source: SOURCE,
        paint: {
          "circle-radius": ["get", "radius"],
          "circle-color": ["get", "color"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: "county-risk-values", type: "symbol", source: SOURCE,
        layout: {
          "text-field": ["get", "value"],
          "text-size": 12,
          "text-allow-overlap": true,
          "text-ignore-placement": true,
        },
        paint: { "text-color": "#ffffff" },
      });
      map.addLayer({
        id: "county-risk-names", type: "symbol", source: SOURCE,
        layout: {
          "text-field": ["get", "name"],
          "text-size": 11,
          "text-offset": [0, 2.7],
          "text-anchor": "top",
          "text-allow-overlap": true,
          "text-ignore-placement": true,
        },
        paint: { "text-color": "#161616", "text-halo-color": "#ffffff", "text-halo-width": 2 },
      });
      map.on("click", (event) => {
        const hit = map.queryRenderedFeatures(event.point, { layers: CLICK_LAYERS })[0];
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

  useEffect(() => {
    const source = mapRef.current?.getSource(SOURCE) as mapboxgl.GeoJSONSource | undefined;
    source?.setData(points(forecasts, activeFips, activeWindow));
  }, [forecasts, activeFips, activeWindow]);

  useEffect(() => {
    if (previousFips.current === activeFips) return;
    previousFips.current = activeFips;
    const county = COUNTIES[activeFips];
    const map = mapRef.current;
    if (county && map) map.flyTo({ center: [county.lon, county.lat], zoom: Math.max(map.getZoom(), 6.6), duration: 700 });
  }, [activeFips]);

  const windowLabel = activeWindow === "next_24h" ? "NEXT 24H" : activeWindow === "next_24_48h" ? "24–48H" : "48–72H";
  return (
    <div className="risk-map-wrap">
      <div className="risk-map-header">
        <span className="section-kicker">COUNTY RISK MAP · {windowLabel}</span>
        <p>Outage probability by county — click any marker to switch the forecast view.</p>
      </div>
      <div className="risk-map-canvas">
        {!accessToken && <div className="risk-map-key-notice">Add a Mapbox public token as <code>VITE_MAPBOX_ACCESS_TOKEN</code> in <code>.env.development.local</code> to show Carbon’s Gray 10 map.</div>}
        <div ref={containerRef} style={{ height: "440px", width: "100%" }} />
        <div className="risk-map-legend">
          <span className="section-kicker">RISK</span>
          {[
            { label: "Low < 35%", color: "#198038" },
            { label: "Mod 35–60%", color: "#b28600" },
            { label: "High 60–80%", color: "#ff832b" },
            { label: "Severe > 80%", color: "#da1e28" },
          ].map(({ label, color }) => <span key={label} className="risk-legend-item"><span className="risk-legend-dot" style={{ background: color }} />{label}</span>)}
          <span className="risk-legend-note">Larger = higher risk · Click to select</span>
        </div>
      </div>
    </div>
  );
}
