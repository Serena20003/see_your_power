import mapboxgl from "mapbox-gl";

export const MAP_ASSETS_URL = ((import.meta.env.VITE_MAP_ASSETS_URL as string | undefined)
  ?? "https://storage.googleapis.com/runway-515115-map-tiles").replace(/\/$/, "");

export type RasterWindow = "next_24h" | "next_24_48h" | "next_48_72h";
type Corners = [[number, number], [number, number], [number, number], [number, number]];
export interface CanopyMap {
  schema_version: "canopy-map/v1";
  tiles: string;
  bounds: [number, number, number, number];
  minzoom: number;
  maxzoom: number;
  tile_size: number;
  vintage: string;
  attribution: string;
}
export interface WeatherMap {
  schema_version: "weather-map/v1";
  fixture: false;
  selected_run: string;
  batch_id: string;
  grid_resolution_degrees: number;
  coverage_summary?: { analysis_land_cells: number; scored_land_cells: number; missing_environment_cells: number } | null;
  coordinates: Corners;
  attribution: string;
  windows: Record<RasterWindow, { image: string; start: string; end: string }>;
}

export function mapAsset(path: string): string {
  return `${MAP_ASSETS_URL}/${path}`;
}

export function showMapLayer(map: mapboxgl.Map | null, id: string, visible: boolean) {
  if (map?.getLayer(id)) map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
}

function belowWater(map: mapboxgl.Map): string | undefined {
  const layers = map.getStyle().layers ?? [];
  // The source raster includes coastal water coded as zero canopy. Retain the
  // basemap's own water mask above both rasters so its shoreline stays aligned.
  return layers.find((layer) => layer.type === "fill" && layer["source-layer"] === "water")?.id
    ?? layers.find((layer) => layer.type === "symbol")?.id;
}

export function installCanopy(map: mapboxgl.Map, metadata: CanopyMap, visible: boolean) {
  const source = map.getSource("canopy-raster") as mapboxgl.RasterTileSource | undefined;
  if (source) {
    map.removeLayer("canopy-raster");
    map.removeSource("canopy-raster");
  }
  map.addSource("canopy-raster", {
    type: "raster", tiles: [mapAsset(metadata.tiles)], tileSize: metadata.tile_size,
    minzoom: metadata.minzoom, maxzoom: metadata.maxzoom,
    bounds: metadata.bounds, attribution: metadata.attribution,
  });
  map.addLayer({ id: "canopy-raster", type: "raster", source: "canopy-raster",
    layout: { visibility: visible ? "visible" : "none" },
    paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 },
  }, map.getLayer("weather-risk-raster") ? "weather-risk-raster"
    : belowWater(map));
}

export function installWeather(map: mapboxgl.Map, metadata: WeatherMap, window: RasterWindow, visible: boolean) {
  const options = { url: mapAsset(metadata.windows[window].image), coordinates: metadata.coordinates };
  const source = map.getSource("weather-risk-raster") as mapboxgl.ImageSource | undefined;
  if (source) { source.updateImage(options); return; }
  map.addSource("weather-risk-raster", { type: "image", ...options });
  map.addLayer({ id: "weather-risk-raster", type: "raster", source: "weather-risk-raster",
    layout: { visibility: visible ? "visible" : "none" },
    paint: { "raster-resampling": "nearest", "raster-fade-duration": 0 },
  }, belowWater(map));
}

export async function fetchMapMetadata<T extends { schema_version: string }>(path: string, schema: string): Promise<T> {
  const response = await fetch(mapAsset(path), { cache: "no-store" });
  if (!response.ok) throw new Error(`Map data unavailable (${response.status})`);
  const data = await response.json() as T & { fixture?: boolean };
  if (data.schema_version !== schema || data.fixture === true) throw new Error("Unsupported map data");
  return data;
}
