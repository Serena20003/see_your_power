# Household chat

Chat uses the signed-in resident's stored profile name, selected location's device inventory and notes, current saved checklist, recent conversation, and available county forecast as context. The latest chat message remains the final user turn. The server reloads Supabase context on every request; it does not accept client-supplied profile or inventory snapshots.

## Editable data

The only chat operations are setting or adjusting a device quantity and adding, editing, deleting, or completing action items. Addresses, contact information, names, account settings, and all other profile fields are excluded from the operation schema and the database patch allowlist. Questions and ambiguous requests should produce an answer or clarification without an operation.

The server sends the resident's access token to Supabase, verifies it through `/auth/v1/user`, and retains existing row-level security. It never uses a service-role key. Device and checklist changes share one location-row update; invalid operations prevent the entire proposal from being written. Every location edit increments `assistant_revision`, so a concurrent chat or profile edit causes a conflict rather than overwriting a newer value.

Actions have stable IDs and persisted completion flags. A chat addition appends to existing actions. Ordinary questions do not replace the checklist. Removing the last action leaves a saved empty list; it does not cause automatic regeneration. Switching locations resets chat history and loads that location's persisted plan.

## Deployment

Apply `supabase/migrations/002_household_chat.sql` to the same Supabase project as the application. It adds `locations.plan_items`, `locations.assistant_revision`, and a revision trigger. No existing user or location rows are deleted and no RLS policies change. Then restart the Python API and reload the frontend. The backend accepts `SUPABASE_URL` and `SUPABASE_ANON_KEY`, falling back to the existing local `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` values. Keep Watsonx credentials on the backend.

If the migration is missing, the API returns an explicit storage-setup error. The UI does not claim an edit succeeded when the database failed. A network timeout after a database commit can be ambiguous; reload the saved state before retrying a relative quantity adjustment.

## Checks

- `PYTHONDONTWRITEBYTECODE=1 model/.venv/bin/python -m pytest model/tests -q`
- `npm run build`
- Run `supabase/tests/household_chat.test.sql` after migrations in a disposable Postgres/Supabase database. This verifies persisted completion, revision conflict handling, and cross-user RLS isolation; fixtures roll back.

Live Watsonx checks can use synthetic inventory and messages without changing user data. End-to-end live Supabase writes additionally require the migration and an authenticated test account.
