-- 001_initial_schema.sql
-- Creates the users and locations tables with RLS policies.
-- Run against your Supabase project via the Supabase CLI:
--   supabase db push

-- ─── Extensions ────────────────────────────────────────────────────────────
-- pgcrypto is bundled in Supabase; gen_random_uuid() is available by default.

-- ─── users ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.users (
  id                  uuid PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
  first_name          text,
  last_name           text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  -- default_location_id references locations, which doesn't exist yet.
  -- Declared DEFERRABLE so the circular FK is validated at transaction end,
  -- not on each individual INSERT.
  default_location_id uuid
);

-- ─── locations ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.locations (
  id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid        NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
  name              text        NOT NULL,
  description       text,
  address           text,
  lat               float8,
  lon               float8,
  fips_code         text,                       -- loosely coupled; nullable
  electronic_items  jsonb       NOT NULL DEFAULT '[]'::jsonb,
  additional_notes  text,
  created_at        timestamptz NOT NULL DEFAULT now()
);

-- Add the circular FK from users → locations now that locations exists.
ALTER TABLE public.users
  ADD CONSTRAINT users_default_location_id_fkey
  FOREIGN KEY (default_location_id)
  REFERENCES public.locations (id)
  ON DELETE SET NULL
  DEFERRABLE INITIALLY DEFERRED;

-- ─── Row-Level Security ─────────────────────────────────────────────────────
ALTER TABLE public.users    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.locations ENABLE ROW LEVEL SECURITY;

-- users: a user may only read and update their own row
CREATE POLICY "users_select_own"
  ON public.users
  FOR SELECT
  USING (id = auth.uid());

CREATE POLICY "users_insert_own"
  ON public.users
  FOR INSERT
  WITH CHECK (id = auth.uid());

CREATE POLICY "users_update_own"
  ON public.users
  FOR UPDATE
  USING (id = auth.uid())
  WITH CHECK (id = auth.uid());

-- locations: a user may only access their own rows
CREATE POLICY "locations_select_own"
  ON public.locations
  FOR SELECT
  USING (user_id = auth.uid());

CREATE POLICY "locations_insert_own"
  ON public.locations
  FOR INSERT
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "locations_update_own"
  ON public.locations
  FOR UPDATE
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());

CREATE POLICY "locations_delete_own"
  ON public.locations
  FOR DELETE
  USING (user_id = auth.uid());

-- ─── Indexes ────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS locations_user_id_idx
  ON public.locations (user_id);

CREATE INDEX IF NOT EXISTS locations_created_at_idx
  ON public.locations (user_id, created_at);
