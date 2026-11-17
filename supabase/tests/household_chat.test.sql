-- Run after migrations in a disposable test database. All fixture changes roll back.
BEGIN;
INSERT INTO auth.users (id) VALUES
 ('aaaaaaaa-1111-1111-1111-111111111111'), ('bbbbbbbb-2222-2222-2222-222222222222');
INSERT INTO public.users (id, first_name, last_name) VALUES
 ('aaaaaaaa-1111-1111-1111-111111111111', 'Alice', 'Resident');
INSERT INTO public.locations (id, user_id, name, address, additional_notes, electronic_items) VALUES
 ('cccccccc-3333-3333-3333-333333333333', 'aaaaaaaa-1111-1111-1111-111111111111', 'Home', 'Unchanged address', 'Unchanged notes', '[{"name":"CPAP machine","number_owned":2}]'),
 ('dddddddd-4444-4444-4444-444444444444', 'bbbbbbbb-2222-2222-2222-222222222222', 'Other home', 'Other address', NULL, '[]');
SET LOCAL ROLE authenticated;
SET LOCAL "request.jwt.claims" = '{"sub":"aaaaaaaa-1111-1111-1111-111111111111","role":"authenticated"}';
DO $$
DECLARE affected integer;
BEGIN
 UPDATE public.locations SET electronic_items = '[{"name":"CPAP machine","number_owned":1}]',
   plan_items = '[{"id":"step-1","title":"Review backup options","completed":true}]'
 WHERE id = 'cccccccc-3333-3333-3333-333333333333' AND assistant_revision = 0;
 GET DIAGNOSTICS affected = ROW_COUNT;
 IF affected <> 1 THEN RAISE EXCEPTION 'Own household save failed'; END IF;
 IF (SELECT assistant_revision FROM public.locations WHERE id='cccccccc-3333-3333-3333-333333333333') <> 1
   THEN RAISE EXCEPTION 'Revision did not advance'; END IF;
 IF (SELECT address FROM public.locations WHERE id='cccccccc-3333-3333-3333-333333333333') <> 'Unchanged address'
   THEN RAISE EXCEPTION 'Device update changed address'; END IF;
 UPDATE public.locations SET plan_items='[]' WHERE id='cccccccc-3333-3333-3333-333333333333' AND assistant_revision=0;
 GET DIAGNOSTICS affected = ROW_COUNT;
 IF affected <> 0 THEN RAISE EXCEPTION 'Stale edit overwrote checklist'; END IF;
 IF (SELECT plan_items->0->>'completed' FROM public.locations WHERE id='cccccccc-3333-3333-3333-333333333333') <> 'true'
   THEN RAISE EXCEPTION 'Completion did not persist'; END IF;
 UPDATE public.locations SET electronic_items='[]' WHERE id='dddddddd-4444-4444-4444-444444444444';
 GET DIAGNOSTICS affected = ROW_COUNT;
 IF affected <> 0 THEN RAISE EXCEPTION 'Cross-user write permitted'; END IF;
 IF (SELECT count(*) FROM public.locations WHERE id='dddddddd-4444-4444-4444-444444444444') <> 0
   THEN RAISE EXCEPTION 'Cross-user read permitted'; END IF;
 -- The profile editor also invalidates outstanding chat proposals.
 UPDATE public.locations SET additional_notes='Updated by profile editor' WHERE id='cccccccc-3333-3333-3333-333333333333';
 IF (SELECT assistant_revision FROM public.locations WHERE id='cccccccc-3333-3333-3333-333333333333') <> 2
   THEN RAISE EXCEPTION 'Profile edit did not invalidate stale chat'; END IF;
END;
$$;
RESET ROLE;
ROLLBACK;
