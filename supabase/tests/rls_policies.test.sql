-- rls_policies.test.sql
-- pgTAP tests for RLS policies on public.users and public.locations.
--
-- Prerequisites:
--   1. pgTAP extension installed in the database:
--        CREATE EXTENSION IF NOT EXISTS pgtap;
--   2. Run via pg_prove or the Supabase CLI:
--        pg_prove -U postgres supabase/tests/rls_policies.test.sql
--
-- The tests create two distinct auth identities (user_a, user_b), insert
-- fixture rows, then verify cross-user reads/writes are blocked by RLS.

BEGIN;

SELECT plan(10);

-- ─── Fixture helpers ─────────────────────────────────────────────────────────

-- Two distinct UUIDs representing user_a and user_b
DO $$
BEGIN
  -- Insert into auth.users so foreign keys resolve
  INSERT INTO auth.users (id, email, created_at, updated_at, role, aud)
  VALUES
    ('aaaaaaaa-0000-0000-0000-000000000001'::uuid, 'user_a@test.local', now(), now(), 'authenticated', 'authenticated'),
    ('bbbbbbbb-0000-0000-0000-000000000002'::uuid, 'user_b@test.local', now(), now(), 'authenticated', 'authenticated')
  ON CONFLICT (id) DO NOTHING;
END $$;

-- Create user rows (simulating what the app does on sign-up)
INSERT INTO public.users (id, first_name, last_name)
VALUES
  ('aaaaaaaa-0000-0000-0000-000000000001'::uuid, 'Alice', 'Aardvark'),
  ('bbbbbbbb-0000-0000-0000-000000000002'::uuid, 'Bob',   'Bobcat')
ON CONFLICT (id) DO NOTHING;

-- Create location rows owned by each user
INSERT INTO public.locations (id, user_id, name, address)
VALUES
  ('cccccccc-0000-0000-0000-000000000003'::uuid, 'aaaaaaaa-0000-0000-0000-000000000001'::uuid, 'Alice Home', '123 Main St'),
  ('dddddddd-0000-0000-0000-000000000004'::uuid, 'bbbbbbbb-0000-0000-0000-000000000002'::uuid, 'Bob Home',   '456 Elm St')
ON CONFLICT (id) DO NOTHING;

-- ─── users RLS tests ─────────────────────────────────────────────────────────

-- Test 1: user_a can read their own users row
SET LOCAL role TO authenticated;
SET LOCAL "request.jwt.claims" TO '{"sub":"aaaaaaaa-0000-0000-0000-000000000001","role":"authenticated"}';

SELECT is(
  (SELECT count(*)::int FROM public.users WHERE id = 'aaaaaaaa-0000-0000-0000-000000000001'),
  1,
  'user_a can SELECT their own row in public.users'
);

-- Test 2: user_a cannot read user_b's users row
SELECT is(
  (SELECT count(*)::int FROM public.users WHERE id = 'bbbbbbbb-0000-0000-0000-000000000002'),
  0,
  'user_a cannot SELECT user_b''s row in public.users'
);

-- Test 3: user_a cannot UPDATE user_b's users row
SELECT throws_ok(
  $$UPDATE public.users SET first_name = 'Hacked' WHERE id = 'bbbbbbbb-0000-0000-0000-000000000002'$$,
  NULL,
  NULL,
  'user_a cannot UPDATE user_b''s row in public.users (UPDATE should affect 0 rows or be blocked)'
);

-- ─── locations RLS tests ──────────────────────────────────────────────────────

-- Test 4: user_a can read their own locations
SELECT is(
  (SELECT count(*)::int FROM public.locations WHERE user_id = 'aaaaaaaa-0000-0000-0000-000000000001'),
  1,
  'user_a can SELECT their own locations'
);

-- Test 5: user_a cannot read user_b's locations
SELECT is(
  (SELECT count(*)::int FROM public.locations WHERE user_id = 'bbbbbbbb-0000-0000-0000-000000000002'),
  0,
  'user_a cannot SELECT user_b''s locations'
);

-- Test 6: user_a cannot read user_b's location by id
SELECT is(
  (SELECT count(*)::int FROM public.locations WHERE id = 'dddddddd-0000-0000-0000-000000000004'),
  0,
  'user_a cannot SELECT user_b''s location by id'
);

-- Test 7: user_a cannot insert a location claiming user_b as owner
SELECT throws_ok(
  $$INSERT INTO public.locations (user_id, name) VALUES ('bbbbbbbb-0000-0000-0000-000000000002', 'Injected')$$,
  NULL,
  NULL,
  'user_a cannot INSERT a location with user_b''s user_id'
);

-- Test 8: user_a cannot update user_b's location
-- (UPDATE silently affects 0 rows when RLS blocks; check that no rows changed)
UPDATE public.locations SET name = 'Tampered' WHERE id = 'dddddddd-0000-0000-0000-000000000004';
SELECT is(
  (SELECT name FROM public.locations WHERE id = 'dddddddd-0000-0000-0000-000000000004'),
  NULL,
  'user_a cannot UPDATE user_b''s location (row not visible after attempted update)'
);

-- Test 9: user_a cannot delete user_b's location
DELETE FROM public.locations WHERE id = 'dddddddd-0000-0000-0000-000000000004';
-- Switch to user_b's perspective to confirm the row still exists
SET LOCAL "request.jwt.claims" TO '{"sub":"bbbbbbbb-0000-0000-0000-000000000002","role":"authenticated"}';
SELECT is(
  (SELECT count(*)::int FROM public.locations WHERE id = 'dddddddd-0000-0000-0000-000000000004'),
  1,
  'user_a cannot DELETE user_b''s location (row still exists when viewed as user_b)'
);

-- Test 10: user_b can read their own locations
SELECT is(
  (SELECT count(*)::int FROM public.locations WHERE user_id = 'bbbbbbbb-0000-0000-0000-000000000002'),
  1,
  'user_b can SELECT their own locations'
);

-- ─── Teardown ────────────────────────────────────────────────────────────────

SELECT * FROM finish();

ROLLBACK;
