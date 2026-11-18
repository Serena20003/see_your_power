// src/types/database.ts
// TypeScript types mirroring the Supabase database schema defined in
// supabase/migrations/001_initial_schema.sql.
//
// NOTE: src/types.ts is intentionally untouched — it holds API/forecast types
// unrelated to user data.

// ─── Domain types ─────────────────────────────────────────────────────────────

/** One electronic device owned at a location. Stored as JSONB in locations.electronic_items. */
export interface ElectronicItem {
  name: string;
  number_owned: number;
}

/** Persisted checklist step for a saved location. */
export interface HouseholdAction {
  id: string;
  title: string;
  title_en: string;
  when: 'two_days_before' | 'night_before' | 'one_hour_before' | 'during_outage';
  reason: string;
  reason_en: string;
  completed: boolean;
}

/** Mirrors the public.users row (email lives in auth.users, not here). */
export interface UserProfile {
  id: string;
  first_name: string | null;
  last_name: string | null;
  created_at: string;
  default_location_id: string | null;
}

/** Mirrors the public.locations row. */
export interface Location {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  address: string | null;
  lat: number | null;
  lon: number | null;
  /** Loosely coupled county FIPS code — may be null; no business logic hard-depends on it. */
  fips_code: string | null;
  electronic_items: ElectronicItem[];
  plan_items?: HouseholdAction[] | null;
  assistant_revision?: number;
  additional_notes: string | null;
  created_at: string;
}

// ─── Supabase Database generic type ──────────────────────────────────────────
// Used to type the createClient<Database>() call in src/lib/supabase/client.ts.
// Expand this as more tables are added.

export interface Database {
  public: {
    Tables: {
      users: {
        Row: UserProfile;
        Insert: Omit<UserProfile, 'created_at'> & { created_at?: string };
        Update: Partial<Omit<UserProfile, 'id'>>;
      };
      locations: {
        Row: Location;
        Insert: Omit<Location, 'id' | 'created_at'> & { id?: string; created_at?: string };
        Update: Partial<Omit<Location, 'id' | 'user_id' | 'created_at'>>;
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: Record<string, never>;
  };
}
