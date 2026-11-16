// src/lib/mapbox/geocode.ts
// Utility for Mapbox Geocoding API — converts an address string to coordinates.

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN as string;

export interface GeocodeResult {
  lat: number;
  lon: number;
  fips_code?: string;
}

/**
 * Geocodes a free-form address string using the Mapbox Geocoding API.
 * Returns lat/lon and optionally a loosely-derived fips_code from context fields.
 * Throws an error if no result is found — the caller should treat this as a geocode failure.
 */
export async function geocodeAddress(address: string): Promise<GeocodeResult> {
  const encoded = encodeURIComponent(address);
  const url = `https://api.mapbox.com/geocoding/v5/mapbox.places/${encoded}.json?access_token=${MAPBOX_TOKEN}&limit=1`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Mapbox Geocoding API error: ${response.status} ${response.statusText}`);
  }

  const json = await response.json();
  const feature = json?.features?.[0];

  if (!feature) {
    throw new Error('Address not found. Please check the address and try again.');
  }

  const [lon, lat] = feature.geometry.coordinates as [number, number];

  // Loosely derive a FIPS-like code from context if available.
  // The short_code on a region context (e.g. "US-CA") or a district context may contain it.
  // This is best-effort and nullable — no business logic hard-depends on it.
  let fips_code: string | undefined;
  const contexts: Array<{ id: string; short_code?: string; text?: string }> =
    feature.context ?? [];
  const districtCtx = contexts.find((c) => c.id?.startsWith('district.'));
  if (districtCtx?.short_code) {
    fips_code = districtCtx.short_code;
  }

  return { lat, lon, ...(fips_code !== undefined ? { fips_code } : {}) };
}
