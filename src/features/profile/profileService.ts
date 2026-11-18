// src/features/profile/profileService.ts
// CRUD helpers for the public.users table.

import { supabase } from '../../lib/supabase/client';
import type { UserProfile } from '../../types/database';

/**
 * Fetches the current user's profile row from public.users.
 * Returns null if the row does not exist yet (e.g. brand-new user pre-onboarding).
 */
export async function getProfile(): Promise<UserProfile | null> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  const { data, error } = await supabase
    .from('users')
    .select('*')
    .eq('id', user.id)
    .maybeSingle();

  if (error) {
    console.error('profileService.getProfile:', error.message);
    return null;
  }
  return data ?? null;
}

/**
 * Creates or updates the current user's profile row.
 * Uses upsert with onConflict: 'id' so it works for both create (onboarding) and update (profile settings).
 */
export async function upsertProfile(data: { first_name: string; last_name: string }): Promise<UserProfile | null> {
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const { error: upsertError } = await (supabase.from('users') as any).upsert(
    { id: user.id, first_name: data.first_name, last_name: data.last_name },
    { onConflict: 'id' },
  );

  if (upsertError) {
    console.error('profileService.upsertProfile:', upsertError.message);
    return null;
  }

  // Re-fetch to return the full row
  return getProfile();
}
