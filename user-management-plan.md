# User Management Plan — Runway

## Overview

Extend the Runway web app with persistent user management backed by Supabase. Authenticated users can log in via email OTP, complete an onboarding flow, and persist their household locations and electronic devices. The app uses one route (`/`) for both the unauthenticated and authenticated experience — auth state gates which panels render (plan + watsonx assistant hidden when logged out). The forecast map, zipcode/address search, and model sources page remain fully accessible without login. Design should follow the IBM Carbon Design System.

**Scope:** `src/`, `supabase/`, `.env.development.local`, `package.json`. Never touches `model/` or `power_outage_model/`.

**Branch:** `feature/user_management`

**Review gate:** Each commit sub-task requires explicit user review and approval before the next sub-task begins. When the user approves a sub-task, commit all changes for that sub-task to `feature/user_management` before beginning the next sub-task.

**Design gate:** Before any sub-task is implemented, a lo-fi SVG mockup sequence must be produced and approved. Each mockup shows the screens involved in that sub-task as simple SVG diagrams with labeled arrows indicating what action/event triggers the transition to the next screen. Implementation begins only after the user approves the mockups.

---

## Decisions Log

| # | Decision |
|---|---|
| Q1 | Add `react-router-dom`; replace `main.tsx` pathname check |
| Q2 | Onboarding captures one location minimum; user may add more before finishing. Survey redesigned: free-form device name + count (7 checkboxes become a starting template) |
| Q3 | Store `address` text + `lat`/`lon` in locations table. Geocode once on save via Mapbox. Map flies to lat/lon on location select. FIPS county coupling is loose and easily removable |
| Q4 | Plan driven by selected location's electronic items. `mockModel.ts` fully replaced. Unauthenticated users see map only — no plan, no assistant |
| Q5 | Saved locations dropdown + separate address search input. Selecting saved location updates map AND plan. Temporary search moves map only. Default location set by user in profile settings (falls back to first created) |
| Q6 | One combined login/signup page at `/login`. Gate: profile row completeness (missing/incomplete name → onboarding, complete → app). Inline error on wrong/expired OTP + "Resend code" option |
| Q7 | Profile settings at `/profile` full page route. Device editing reuses redesigned onboarding survey UI. Source badges removed |
| Q8 | Unauthenticated: same shell, plan + assistant hidden. Address search replaces CountySelector. Visible "Log in / Sign up" button in header for unauthenticated users |
| Q9 | `users.id` = `auth.users.id` UUID. `electronic_items` as JSONB column. `default_location_id` on users table |
| Q10 | pgTAP SQL tests for RLS policies |
| Q11 | Geocode on "Save"/"Next" click. Block save if geocoding fails — show inline error |
| Q12 | `email` removed from users table (lives in `auth.users`). `fips_code` text column added to locations |
| Q13 | Valid session → go directly to `/` (app renders logged-in state). Expired session on protected route → redirect to `/`. `/login` is a separate route |
| Q14 | Commit 7 split: unauthenticated view is its own commit. User reviews after each commit |
| Q15 | Landing and dashboard are the same route `/`. Auth state gates panel visibility. "In-app" state indicated by profile circle (initials), greeting, and location-aware map |

---

## Final File Structure

```
src/
  components/          # Shared IBM Carbon styled components
  features/
    auth/
      components/      # EmailForm (OtpInput removed — magic-link flow)
      authService.ts   # sendOtp(), verifyOtp(), checkProfileComplete()
    profile/
      components/      # ProfileForm
      profileService.ts  # getProfile(), upsertProfile()
    locations/
      components/      # LocationForm, LocationsList
      locationService.ts  # CRUD for locations
      useLocations.ts  # State and fetching hook
  lib/
    supabase/          # Supabase client singleton
  pages/
    LoginPage.tsx
    OnboardingPage.tsx
    ProfilePage.tsx
    AppPage.tsx        # Main / route (was App.tsx)
  types/               # Database and domain types (replaces mockModel.ts types)
  App.tsx              # Router root (replaces current App.tsx)
  main.tsx
  ...existing files preserved

supabase/
  migrations/
    001_initial_schema.sql
  tests/
    rls_policies.test.sql

.env.development.local  # Add VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY
package.json            # Add @supabase/supabase-js, react-router-dom
```

---

## Database Schema

### `public.users`
| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | = `auth.users.id` |
| `first_name` | text | |
| `last_name` | text | |
| `created_at` | timestamptz | default now() |
| `default_location_id` | uuid FK → locations | nullable |

### `public.locations`
| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid PK | default gen_random_uuid() |
| `user_id` | uuid FK → auth.users | not null |
| `name` | text | e.g. "Home", "Parents' Home" |
| `description` | text | |
| `address` | text | user-entered |
| `lat` | float8 | geocoded on save |
| `lon` | float8 | geocoded on save |
| `fips_code` | text | loosely coupled county code |
| `electronic_items` | jsonb | array of `{name: string, number_owned: number}` |
| `additional_notes` | text | |
| `created_at` | timestamptz | default now() |

### RLS Policies
- `users`: authenticated user can SELECT/UPDATE their own row (`id = auth.uid()`)
- `locations`: authenticated user can SELECT/INSERT/UPDATE/DELETE where `user_id = auth.uid()`

---

## Sub-Tasks

---

### Sub-Task 1 — Supabase Schema & Infrastructure
**Status:** `[x] done`
**Design mockup:** not applicable (no UI screens in this sub-task — schema and infra only)

**Intent:** Establish the database foundation. All subsequent sub-tasks depend on the schema being correct. Getting this right first prevents migrations that break later commits.

**Expected Outcomes:**
- `supabase/migrations/001_initial_schema.sql` creates `users` and `locations` tables with correct columns, FK constraints, and RLS policies
- `supabase/tests/rls_policies.test.sql` contains pgTAP tests verifying: a user cannot read another user's row in `users`; a user cannot read/write another user's locations
- `.env.development.local` gains `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` placeholder entries
- `package.json` gains `@supabase/supabase-js` dependency
- `src/lib/supabase/client.ts` exports a typed Supabase client singleton
- `src/types/database.ts` exports TypeScript types mirroring the schema (`UserProfile`, `Location`, `ElectronicItem`)

**Todo List:**
1. Create `supabase/migrations/001_initial_schema.sql` — `users` table, `locations` table, enable RLS, write policies
2. Create `supabase/tests/rls_policies.test.sql` — pgTAP tests for `users` and `locations` RLS
3. Add `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` to `.env.development.local` (placeholder values; user fills in real keys)
4. Add `@supabase/supabase-js` to `package.json` dependencies
5. Create `src/lib/supabase/client.ts` — typed Supabase client using env vars
6. Create `src/types/database.ts` — `ElectronicItem`, `UserProfile`, `Location` TypeScript types

**Relevant Context:**
- Schema decisions: see Decisions Log Q9, Q12
- `fips_code` is nullable and loosely coupled — no business logic should hard-depend on it
- `default_location_id` has a circular FK (users → locations → users); handle with `DEFERRABLE INITIALLY DEFERRED` or set after insert
- Existing `src/types.ts` stays untouched — it holds API/forecast types unrelated to user data

---

### Sub-Task 2 — Dependencies & Routing
**Status:** `[x] done`
**Design mockup:** not applicable (stub pages only — no visible UI changes)

**Intent:** Wire up React Router and lay the route skeleton so all subsequent page sub-tasks have a home. This commit establishes the navigation architecture without changing any visible UI.

**Expected Outcomes:**
- `react-router-dom` added to `package.json`
- `main.tsx` uses `<BrowserRouter>` and replaces the pathname check
- Routes defined: `/` → `AppPage`, `/login` → `LoginPage`, `/onboarding` → `OnboardingPage` (protected), `/profile` → `ProfilePage` (protected), `/model-sources` → `ModelSources`
- A `ProtectedRoute` component redirects to `/` if no active session
- `AppPage.tsx`, `LoginPage.tsx`, `OnboardingPage.tsx`, `ProfilePage.tsx` created as stubs (render a placeholder `<div>`)
- Existing `App.tsx` logic is **not yet moved** — `AppPage` is a stub
- App still runs and `/model-sources` still works

**Todo List:**
1. Add `react-router-dom` to `package.json`
2. Create `src/components/ProtectedRoute.tsx` — checks Supabase session, redirects to `/` if unauthenticated
3. Create stub page files: `src/pages/AppPage.tsx`, `src/pages/LoginPage.tsx`, `src/pages/OnboardingPage.tsx`, `src/pages/ProfilePage.tsx`
4. Rewrite `main.tsx` to use `<BrowserRouter>` with all five routes
5. Verify `/model-sources` still renders `ModelSources` and app compiles without errors

**Relevant Context:**
- Current `main.tsx` is 16 lines with a simple pathname check — full replacement
- `ProtectedRoute` reads session from `src/lib/supabase/client.ts` (Sub-Task 1)
- On session check: show a brief full-screen loading spinner while async `getSession()` resolves, then render or redirect
- Valid session on load → app renders at `/` in logged-in state (no redirect needed)

---

### Sub-Task 3 — Auth Feature
**Status:** `[x] done`
**Design mockup required:** `[x] approved`
Screens to mockup:
- `/login` — Step 1: email entry form (header with login button, centered card, email input, submit)
- `/login` — Step 2: OTP code entry (same card, code input, inline error state, resend link)
- Transition arrows: "Submit email" → Step 2; "Wrong code" → inline error; "Resend code" → clears input + success note; "Correct code + complete profile" → `/`; "Correct code + incomplete profile" → `/onboarding`

**Additional requirement (approved after mockup):** Validate email before sending OTP — (1) client-side format check, (2) Abstract API email validation (`emailvalidation.abstractapi.com/v1/`) to block disposable/temporary emails. Add `VITE_ABSTRACT_EMAIL_API_KEY` to `.env.development.local`.

**Intent:** Implement the email login/signup flow. This is the entry point to the entire user system — must be solid before onboarding or dashboard are wired.

**Expected Outcomes:**
- `LoginPage` renders a two-step form: email input → "check your inbox" confirmation
- `authService.ts` exports `sendOtp(email)` and `verifyOtp(email, token)` using Supabase `signInWithOtp` and `verifyOtp`
- After magic link click: `onAuthStateChange` fires `SIGNED_IN` in `LoginPage`; check `users` table for profile completeness → redirect to `/onboarding` if incomplete, `/` if complete
- Carbon Design System components used for inputs and buttons (consistent with existing UI)

**Todo List:**
1. Create `src/features/auth/authService.ts` — `sendOtp()`, `verifyOtp()`, `checkProfileComplete(userId)`
2. Create `src/features/auth/components/EmailForm.tsx` — Carbon `TextInput` + submit button
3. Create `src/features/auth/components/OtpInput.tsx` — Carbon `TextInput` for 6-digit code + "Resend code" link + inline error display
4. Wire `LoginPage.tsx` — renders `EmailForm` then `OtpInput` on submit; handles redirect logic after verify
5. Add `signOut()` to `authService.ts` for use in header later

**Fix (post-implementation):** Supabase free tier does not allow editing email templates without custom SMTP, so the default "Magic link or OTP" template sends a magic link (clickable URL), not a 6-digit code. Switched auth flow to magic link:
- `sendOtp()` now passes `emailRedirectTo: window.location.origin` so the link returns to the correct app origin
- `LoginPage` step 2 changed from `OtpInput` code entry to a "check your inbox" message
- `LoginPage` subscribes to `onAuthStateChange('SIGNED_IN')` to detect the redirect and run the profile-complete check → navigate
- `OtpInput` component retained in the codebase but no longer used by `LoginPage`
- Supabase dashboard: `http://localhost:5173` must be listed under **Authentication → URL Configuration → Redirect URLs**

**Relevant Context:**
- Supabase OTP: `supabase.auth.signInWithOtp({ email })` sends the code; `supabase.auth.verifyOtp({ email, token, type: 'email' })` verifies
- Profile completeness check: query `public.users` where `id = auth.uid()` and `first_name IS NOT NULL`
- Login page should be accessible when logged out; if already logged in, redirect to `/`

---

### Sub-Task 4 — Profile Feature
**Status:** `[x] done`
**Design mockup required:** `[x] approved`
Screens to mockup:
- Header — logged-out state: "Log in / Sign up" button, no greeting, no avatar circle
- Header — logged-in state: initials circle (top-right), greeting "Hello, [first name]" visible
- Transition arrows: "Session resolves (logged in)" → logged-in header; "Click Log in" → `/login`; "Click initials circle" → `/profile`

**Intent:** Implement CRUD for the `users` table (first name, last name). This powers the greeting on the main page, the profile circle initials, and the profile settings page's user info section.

**Expected Outcomes:**
- `profileService.ts` exports `getProfile()`, `upsertProfile({ first_name, last_name })`
- `ProfileForm` component renders editable first name + last name fields with save confirmation
- A React context (`AuthContext`) or equivalent provides `{ user, profile, session, signOut }` to the component tree so any component can read the current user without prop-drilling
- The header circle icon shows the user's initials when logged in; shows "Log in / Sign up" button when logged out

**Todo List:**
1. Create `src/features/profile/profileService.ts` — `getProfile()`, `upsertProfile()`
2. Create `src/features/profile/components/ProfileForm.tsx` — first/last name fields, save button, success/error feedback
3. Create `src/lib/supabase/AuthContext.tsx` — React context providing session, profile, and signOut; wraps the app in `main.tsx`
4. Update the app header (in `App.tsx` or a shared `Header` component) — show initials circle + greeting when logged in, "Log in / Sign up" button when logged out
5. `ProfilePage.tsx` stub updated to render `ProfileForm` for the user info section (locations section comes in Sub-Task 6)

**Relevant Context:**
- Current header in `App.tsx` has `<span className="avatar">R</span>` and "SAMPLE RESIDENT · ROSA MARTINEZ" — these get replaced
- `AuthContext` must handle the async session load on app start (show spinner until resolved)
- `upsertProfile` uses Supabase `upsert` with `onConflict: 'id'` so it works for both create (onboarding) and update (profile settings)

---

### Sub-Task 5 — Locations Feature
**Status:** `[x] done`
**Design mockup required:** `[x] approved`
Screens to mockup:
- `LocationForm` — create mode: name, description, address, additional notes fields; "Save" button; inline geocode error state
- `LocationsList` — list of location cards: name, address, default badge, edit/delete actions
- Transition arrows: "Save (geocode success)" → card added to list; "Save (geocode fail)" → inline error on address field; "Edit" → LocationForm in edit mode; "Delete" → card removed

**Intent:** Implement CRUD for the `locations` table including Mapbox geocoding on save. This is the core data model for the personalized plan.

**Expected Outcomes:**
- `locationService.ts` exports `getLocations()`, `createLocation()`, `updateLocation()`, `deleteLocation()`, `setDefaultLocation(locationId)`
- Geocoding called on save: Mapbox Geocoding API called with the address text; `lat`, `lon`, `fips_code` (optional, loosely coupled) stored from result; save blocked with inline error if geocoding fails
- `useLocations.ts` hook returns `{ locations, defaultLocation, loading, error, refetch }`
- `LocationForm` component handles create/edit: name, description, address (with geocode-on-save), electronic items survey (see Sub-Task 6 for survey UI), additional notes
- `LocationsList` component renders saved locations with edit/delete actions

**Todo List:**
1. Create `src/features/locations/locationService.ts` — full CRUD + `setDefaultLocation()`
2. Create `src/features/locations/useLocations.ts` — fetches on mount, exposes mutations
3. Create `src/features/locations/components/LocationForm.tsx` — name, description, address fields; geocode on submit; inline geocode error handling; electronic items placeholder (wired in Sub-Task 6)
4. Create `src/features/locations/components/LocationsList.tsx` — list with edit/delete, highlights default location
5. Add Mapbox Geocoding API call utility to `src/lib/mapbox/geocode.ts` — takes address string, returns `{ lat, lon }` or throws

**Relevant Context:**
- Mapbox token already in `.env.development.local` as `VITE_MAPBOX_ACCESS_TOKEN`
- Geocoding endpoint: `https://api.mapbox.com/geocoding/v5/mapbox.places/{address}.json?access_token={token}`
- `fips_code` derivation from lat/lon is loosely coupled — it can be derived from a reverse geocode or omitted entirely; do not hard-couple plan generation to it
- `setDefaultLocation` updates `users.default_location_id` via `profileService` or directly

---

### Sub-Task 6 — Device Survey UI (Shared Component)
**Status:** `[x] done`
**Design mockup required:** `[x] approved`
Screens to mockup:
- `DeviceSurvey` — empty state: quick-add suggestion chips (7 device types), "Add device" button
- `DeviceSurvey` — adding a device: inline row appears with name text input + count number input + confirm/cancel
- `DeviceSurvey` — populated state: list of device rows (name, count, edit icon, delete icon)
- Transition arrows: "Click chip" → name pre-filled in inline row; "Confirm" → row added to list; "Cancel" → row dismissed; "Edit icon" → row becomes editable inline; "Delete icon" → row removed

**Intent:** Build the redesigned electronic device survey — a reusable component used in both onboarding and profile settings. Replaces the current 7-checkbox boolean survey with free-form device name + count input.

**Expected Outcomes:**
- `DeviceSurvey` component in `src/components/DeviceSurvey.tsx` accepts `items: ElectronicItem[]` and `onChange: (items: ElectronicItem[]) => void`
- UI: list of current devices (name + count), "Add device" button opens an inline row with a text input (name) and number input (count), confirm/cancel; existing rows have edit and delete actions
- The 7 original profile choices (oxygen concentrator, insulin fridge, EV, etc.) are shown as quick-add suggestion chips/buttons — clicking one pre-fills the name field
- Carbon Design System components throughout

**Todo List:**
1. Create `src/components/DeviceSurvey.tsx` — controlled component, `items` + `onChange` props
2. Add quick-add suggestion chips for the 7 original profile choices (name strings only, no boolean logic)
3. Integrate `DeviceSurvey` into `LocationForm.tsx` (Sub-Task 5) replacing the electronic items placeholder
4. Verify the component renders correctly standalone before onboarding integration

**Relevant Context:**
- `ElectronicItem` type: `{ name: string; number_owned: number }` from `src/types/database.ts`
- Original 7 profile choice labels live in `mockModel.ts` — extract the human-readable names for the suggestion chips; `mockModel.ts` itself will be deleted in Sub-Task 8
- This component must be reused identically in onboarding (Sub-Task 7) and profile settings (Sub-Task 9)

---

### Sub-Task 7 — Onboarding Flow
**Status:** `[x] done`
**Design mockup required:** `[x] approved`
Screens to mockup:
- `/onboarding` Step 1: first name + last name fields, progress indicator at step 1 of 3, "Next" button
- `/onboarding` Step 2: location form (name, description, address, DeviceSurvey), progress indicator at step 2, "Back" / "Next" buttons
- `/onboarding` Step 3: "Add another location?" decision screen — "Yes, add another" / "No, finish" options, progress indicator at step 3
- Transition arrows: "Next (Step 1)" → Step 2; "Next (Step 2, geocode success)" → Step 3; "Next (Step 2, geocode fail)" → inline error; "Yes" → loops back to Step 2; "No, finish" → saves all + redirects to `/`

**Intent:** Wire the full sequential onboarding experience for new users: name → first location (with address + devices) → option to add more locations → finish. This is the first time all auth, profile, locations, and device survey pieces come together.

**Expected Outcomes:**
- `OnboardingPage.tsx` implements a multi-step flow:
  - Step 1: First name + last name (calls `upsertProfile`)
  - Step 2: Add first location — name, description, address (geocoded on Next), device survey via `DeviceSurvey`
  - Step 3: "Add another location?" — if yes, repeats Step 2; if no, finish
- On finish: sets the first added location as default, redirects to `/`
- Progress indicator shown (e.g. Carbon `ProgressIndicator`)
- If user navigates to `/onboarding` while already having a complete profile, redirect to `/`

**Todo List:**
1. Implement `OnboardingPage.tsx` multi-step flow using local state for step index and accumulated location data
2. Step 1: render `ProfileForm` fields inline (not the full ProfileForm component — inline for onboarding context)
3. Step 2: render `LocationForm` with `DeviceSurvey` integrated; geocode on "Next" click; block if geocode fails
4. Step 3: "Add another location?" decision screen — loops back to Step 2 if yes; calls `createLocation()` for each accumulated location on finish
5. On finish: call `setDefaultLocation` for the first location, then navigate to `/`
6. Add redirect guard: if profile already complete, redirect away from `/onboarding`

**Relevant Context:**
- `createLocation()` and `upsertProfile()` from Sub-Tasks 4 and 5
- `DeviceSurvey` from Sub-Task 6
- Onboarding is a protected route — `ProtectedRoute` ensures only authenticated (but profile-incomplete) users reach it; `authService.checkProfileComplete` gates the redirect in `LoginPage` too

---

### Sub-Task 8 — Unauthenticated App View
**Status:** `[x] done`
**Design mockup required:** `[x] approved`
Screens to mockup:
- `/` unauthenticated: full layout — header (logo, address search bar, "Log in / Sign up" button), map (centered LA County), forecast panel; plan + assistant panels absent
- Address search interaction: search bar focused → user types address → "Search" action → map flies to result
- Transition arrows: "Click Log in / Sign up" → `/login`; "Submit address search" → map re-centers (stays on `/`)

**Intent:** Adapt the main `/` page for unauthenticated users. Map and forecast are always visible. Plan and assistant panels are hidden. Address search input replaces `CountySelector`. Login button appears in header. This commit isolates the unauthenticated experience before the authenticated dashboard is wired in Sub-Task 9.

**Expected Outcomes:**
- `AppPage.tsx` renders the existing forecast map and forecast panel for all users (no login required)
- Plan panel and assistant panel are conditionally rendered only when `session` is non-null (from `AuthContext`)
- `CountySelector.tsx` is removed from the unauthenticated view; replaced by an address/zipcode search `TextInput` using Mapbox Geocoding (reuses `geocode.ts` from Sub-Task 5) — moves the map center on submit
- Header shows "Log in / Sign up" button when `session` is null
- Existing `App.tsx` interview/plan/assistant/forecast logic is migrated into `AppPage.tsx`; old `App.tsx` becomes the router root (or is removed if `main.tsx` handles routing directly)
- `mockModel.ts` is deleted; `initialProfile` / `createPlan()` / `Profile` type are no longer used

**Todo List:**
1. Migrate existing `App.tsx` content into `AppPage.tsx`; remove `InterviewPanel` (onboarding replaces it), remove sample Rosa references, remove `mockModel.ts` imports
2. Add auth-conditional rendering: plan panel + assistant panel only render when `session !== null`
3. Replace `CountySelector` with an address search `TextInput` (Mapbox geocode → fly map to result) — same input works for both auth states
4. Add "Log in / Sign up" button to header (visible only when `session === null`); clicking navigates to `/login`
5. Delete `mockModel.ts`
6. Verify app compiles and unauthenticated user sees map + forecast only with login button in header

**Relevant Context:**
- `CountyRiskMap` flies to a lat/lon via its `onSelectFips` prop today — the map fly-to for address search should call a new `onFlyTo({ lat, lon })` prop or similar; keep FIPS coupling loose
- `ForecastPanel` and `useForecast` hook stay unchanged — they still work off a FIPS code internally
- The "greeting" line ("Hello, Rosa...") is hidden when logged out

---

### Sub-Task 9 — Authenticated Dashboard Wiring
**Status:** `[ ] pending`
**Design mockup required:** `[ ] pending approval`
Screens to mockup:
- `/` authenticated: full layout — header (logo, locations dropdown, address search bar, greeting "Hello [name]", initials circle), map (centered on default location), forecast panel, plan panel, assistant panel
- Locations dropdown open: list of saved location names, currently selected highlighted
- Transition arrows: "Select saved location" → map flies to location lat/lon + plan updates; "Submit address search" → map re-centers only (plan stays on selected location); "Click initials circle" → `/profile`

**Intent:** Wire the full logged-in experience: location selector drives the plan and assistant, greeting uses real user name, assistant can mutate electronic devices, map flies to selected location's coordinates.

**Expected Outcomes:**
- When logged in, header shows first+last name initials circle and greeting ("Hello, [first name]...")
- Saved locations dropdown appears (from `useLocations`) — selecting a location: flies the map to that location's lat/lon, sets the active location driving the plan
- Plan is generated from the selected location's `electronic_items` (not from `mockModel.ts` — deleted)
- Watsonx assistant is initialized with the selected location's device list; assistant-driven mutations update the location's `electronic_items` in Supabase and refresh `useLocations`
- Temporary address search still moves the map but does not change the active plan location
- Default location is loaded on app start (from `users.default_location_id`, fallback to first by `created_at`)

**Todo List:**
1. Add locations dropdown to `AppPage.tsx` — uses `useLocations()`; renders only when `session !== null`
2. Wire selected location's `electronic_items` to the plan generation logic (replace `Profile` boolean flags with device list passed to plan engine / API)
3. Update assistant initialization: replace Rosa hardcodes with `profile.first_name` and selected location's device list
4. Implement assistant-driven device mutation: when assistant response includes device add/remove instructions, call `updateLocation()` and trigger `useLocations` refetch
5. Map fly-to on location select: pass lat/lon from selected location to `CountyRiskMap` (new `flyToCoords` prop or equivalent)
6. Load default location on mount via `useLocations` → `defaultLocation`

**Relevant Context:**
- `api.ts` `/v1/plan` payload currently uses `resident.devices[]`, `hasEV`, `livesAlone` — these fields need to be derived from `ElectronicItem[]`; map device names to the existing API field shape or update the payload
- Plan engine (`engine/plan.ts`) is in scope; `engine/` files are NOT deleted but their `Resident` type dependency on `mockModel.ts` must be updated to use `src/types/database.ts` types
- `fips_code` on the location is used loosely here: if present, pass it to `useForecast`; if null, keep current FIPS or default to LA County

---

### Sub-Task 10 — Profile Settings Page
**Status:** `[ ] pending`
**Design mockup required:** `[ ] pending approval`
Screens to mockup:
- `/profile` — user info section: email (read-only), first name + last name fields, save button
- `/profile` — locations section: list of location cards (name, address, default badge, "Set as default" / "Edit" / "Delete" actions), "Add new location" button
- `/profile` — location edit view: LocationForm with DeviceSurvey pre-populated
- Transition arrows: "Save name" → success confirmation; "Edit location" → edit view; "Save location" → back to list; "Set as default" → default badge moves; "Add new location" → blank LocationForm; "Sign out" → `/`; "Back arrow / logo" → `/`

**Intent:** Build the full `/profile` page where users manage their info, view and edit all locations, and update electronic devices per location using the same survey UI.

**Expected Outcomes:**
- `/profile` renders: user info section (email read-only from `auth.users`, first/last name editable via `ProfileForm`), locations list (`LocationsList`), "Add new location" button, per-location edit (opens `LocationForm` + `DeviceSurvey`), ability to set a location as default
- Clicking the initials circle in the header navigates to `/profile`; a "Sign out" button is present
- "YOUR SETTINGS · WHAT RUNWAY KNOWS" panel is removed from the main app view; device list per location in `/profile` replaces it (no source badges)
- After editing a location's devices, `useLocations` refetches and the dashboard plan updates if that location is currently active

**Todo List:**
1. Implement `ProfilePage.tsx` with two sections: user info and locations
2. User info section: email (read-only), `ProfileForm` for name editing
3. Locations section: render `LocationsList`; each location has an "Edit" button opening `LocationForm` with `DeviceSurvey` pre-populated
4. "Add new location" button opens `LocationForm` in create mode
5. "Set as default" action on each location card calls `setDefaultLocation()`
6. "Sign out" button calls `authService.signOut()` and redirects to `/`
7. Header initials circle navigates to `/profile` (add `onClick` → `navigate('/profile')`)
8. Remove "YOUR SETTINGS · WHAT RUNWAY KNOWS" panel from `AppPage.tsx`

**Relevant Context:**
- `LocationForm` and `DeviceSurvey` from Sub-Tasks 5 and 6
- `ProfileForm` from Sub-Task 4
- `LocationsList` from Sub-Task 5
- Email comes from `session.user.email` (Supabase Auth), not from `public.users`
- `useLocations` refetch propagates to the dashboard if `AuthContext` or a shared state holds the active location

---

## Notes for Implementation

- **Never touch** `model/`, `power_outage_model/`, or any file in `engine/` — confirmed: no engine files import from `mockModel.ts`; they import from `src/types.ts` which is untouched
- **Carbon Design System** components must be used for all new UI (consistent with existing `@carbon/react` usage)
- **`.env.development.local`** gains Supabase keys; `.gitignore` already excludes `*.local` files — secrets are safe
- **`fips_code`** on locations is populated as a best-effort from Mapbox reverse geocoding; no plan or forecast logic hard-depends on it
- **`mockModel.ts`** is deleted in Sub-Task 8; ensure no imports remain before deletion
- Each sub-task should result in a **compilable, runnable commit** — no broken intermediate states
- **Abstract API email validation:** Only 100 credits available. Never call the Abstract API in tests, dev smoke tests, or any automated context. It is called only in real user-initiated login flows. No test code should import or invoke `validateEmail()`.
