// src/features/locations/useLocations.ts
// React hook for fetching and managing the current user's locations.

import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../lib/supabase/AuthContext';
import { getLocations } from './locationService';
import type { Location } from '../../types/database';

export interface UseLocationsResult {
  locations: Location[];
  defaultLocation: Location | null;
  loading: boolean;
  error: string | null;
  refetch: () => Promise<void>;
}

/**
 * Fetches all locations for the current user on mount.
 * defaultLocation: the location whose id matches profile.default_location_id,
 * falling back to locations[0] if none is set.
 */
export function useLocations(): UseLocationsResult {
  const { profile } = useAuth();
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getLocations();
      setLocations(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load locations.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const defaultLocation: Location | null =
    locations.find((l) => l.id === profile?.default_location_id) ??
    locations[0] ??
    null;

  return { locations, defaultLocation, loading, error, refetch };
}
