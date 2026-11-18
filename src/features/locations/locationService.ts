// src/features/locations/locationService.ts
// CRUD helpers for the public.locations table.

import { supabase } from '../../lib/supabase/client';
import type { Location } from '../../types/database';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyTable = any;

export type LocationCreateData = Omit<Location, 'id' | 'created_at' | 'user_id'>;
export type LocationUpdateData = Partial<Omit<Location, 'id' | 'user_id' | 'created_at'>>;

/**
 * Returns all locations for the current user, ordered oldest-first.
 */
export async function getLocations(): Promise<Location[]> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { data, error } = await (supabase.from('locations') as AnyTable)
    .select('*')
    .order('created_at', { ascending: true });

  if (error) {
    console.error('locationService.getLocations:', error.message);
    throw error;
  }
  return (data ?? []) as Location[];
}

/**
 * Inserts a new location row for the current user.
 */
export async function createLocation(data: LocationCreateData): Promise<Location> {
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { data: created, error } = await (supabase.from('locations') as AnyTable)
    .insert({ ...data, user_id: user.id })
    .select()
    .single();

  if (error) {
    console.error('locationService.createLocation:', error.message);
    throw error;
  }
  return created as Location;
}

/**
 * Updates an existing location row.
 */
export async function updateLocation(
  id: string,
  data: LocationUpdateData,
): Promise<Location> {
  const { data: updated, error } = await (supabase.from('locations') as AnyTable)
    .update(data)
    .eq('id', id)
    .select()
    .single();

  if (error) {
    console.error('locationService.updateLocation:', error.message);
    throw error;
  }
  return updated as Location;
}

/**
 * Deletes a location row by id.
 */
export async function deleteLocation(id: string): Promise<void> {
  const { error } = await (supabase.from('locations') as AnyTable).delete().eq('id', id);

  if (error) {
    console.error('locationService.deleteLocation:', error.message);
    throw error;
  }
}

/**
 * Sets the current user's default location by updating users.default_location_id.
 */
export async function setDefaultLocation(locationId: string): Promise<void> {
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { error } = await (supabase.from('users') as any).upsert(
    { id: user.id, default_location_id: locationId },
    { onConflict: 'id' },
  );

  if (error) {
    console.error('locationService.setDefaultLocation:', error.message);
    throw error;
  }
}
