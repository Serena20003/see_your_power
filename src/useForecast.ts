/**
 * useForecast.ts — React hook for loading county forecasts from the API.
 *
 * Returns an ApiState<CountyForecast> and a refetch function.
 * Handles loading, success, stale, and error states so components
 * can render appropriate UI without any try/catch.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchForecast } from "./api";
import type { ApiState, CountyForecast } from "./types";

const POLL_INTERVAL_MS = 60 * 60 * 1_000; // re-fetch every hour

export function useForecast(fips: string): {
  state: ApiState<CountyForecast>;
  refetch: () => void;
} {
  const [state, setState] = useState<ApiState<CountyForecast>>({ phase: "idle" });
  const prevRef = useRef<{ data: CountyForecast; fetchedAt: number } | null>(null);

  const load = useCallback(async () => {
    setState({ phase: "loading" });
    const result = await fetchForecast(
      fips,
      prevRef.current?.data,
      prevRef.current?.fetchedAt
    );
    if (result.phase === "success" || result.phase === "stale") {
      prevRef.current = { data: result.data, fetchedAt: result.fetchedAt };
    }
    setState(result);
  }, [fips]);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [load]);

  return { state, refetch: load };
}
