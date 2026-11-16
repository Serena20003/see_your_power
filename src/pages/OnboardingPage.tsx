// src/pages/OnboardingPage.tsx
// Multi-step onboarding flow for new users:
//   Step 1 — Name (first + last)
//   Step 2 — Location (name, description, address, devices, notes)
//   Step 3 — "Add another location?" decision screen
//
// Guards:
//   • ProtectedRoute already redirects unauthenticated users to /.
//   • If profile already complete (first_name + last_name non-empty), redirect to / on mount.

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Button,
  InlineLoading,
  InlineNotification,
  TextInput,
  TextArea,
  Theme,
} from '@carbon/react';
import { Flash } from '@carbon/icons-react';
import { Link } from 'react-router-dom';
import { useAuth } from '../lib/supabase/AuthContext';
import { upsertProfile } from '../features/profile/profileService';
import {
  createLocation,
  setDefaultLocation,
  type LocationCreateData,
} from '../features/locations/locationService';
import { geocodeAddress } from '../lib/mapbox/geocode';
import DeviceSurvey from '../components/DeviceSurvey';
import type { ElectronicItem } from '../types/database';

// ─── Step types ───────────────────────────────────────────────────────────────

type Step = 1 | 2 | 3;

/** Local state for one location being entered in Step 2. */
interface LocationDraft {
  locName: string;
  description: string;
  address: string;
  electronic_items: ElectronicItem[];
  additional_notes: string;
}

function emptyLocationDraft(): LocationDraft {
  return {
    locName: '',
    description: '',
    address: '',
    electronic_items: [],
    additional_notes: '',
  };
}

/** A fully geocoded location, ready to be sent to Supabase. */
type ReadyLocation = Omit<LocationCreateData, 'user_id'>;

// ─── Step dots progress indicator ─────────────────────────────────────────────

function StepDots({ current, total }: { current: Step; total: number }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        marginBottom: '1.5rem',
      }}
      aria-label={`Step ${current} of ${total}`}
    >
      {Array.from({ length: total }, (_, i) => {
        const step = (i + 1) as Step;
        const done = step < current;
        const active = step === current;
        return (
          <span
            key={step}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: active ? '28px' : '20px',
              height: active ? '28px' : '20px',
              borderRadius: '50%',
              fontSize: active ? '12px' : '10px',
              fontWeight: 600,
              color: active ? '#fff' : done ? '#0f62fe' : '#a8a8a8',
              background: active ? '#0f62fe' : done ? '#d9e5ff' : '#e0e0e0',
              border: done ? '2px solid #0f62fe' : '2px solid transparent',
              transition: 'all 0.15s ease',
            }}
            aria-current={active ? 'step' : undefined}
          >
            {done ? '✓' : step}
          </span>
        );
      })}
      <span
        style={{
          marginLeft: '8px',
          fontSize: '12px',
          fontWeight: 600,
          color: '#525252',
          letterSpacing: '0.08em',
        }}
      >
        STEP {current} OF {total}
      </span>
    </div>
  );
}

// ─── Shared card wrapper ──────────────────────────────────────────────────────

function OnboardingCard({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        maxWidth: '540px',
        margin: '0 auto',
        background: '#fff',
        padding: '2.5rem',
        borderTop: '4px solid #0f62fe',
      }}
    >
      {children}
    </div>
  );
}

// ─── Step 1: Name ─────────────────────────────────────────────────────────────

interface Step1Props {
  firstName: string;
  lastName: string;
  onFirstNameChange: (v: string) => void;
  onLastNameChange: (v: string) => void;
  onNext: () => void;
}

function Step1({ firstName, lastName, onFirstNameChange, onLastNameChange, onNext }: Step1Props) {
  const disabled = !firstName.trim() || !lastName.trim();
  return (
    <>
      <StepDots current={1} total={3} />
      <h1
        style={{
          fontSize: '1.75rem',
          fontWeight: 400,
          letterSpacing: '-0.02em',
          margin: '0 0 0.5rem',
        }}
      >
        Welcome to Runway.
      </h1>
      <p style={{ color: '#525252', fontSize: '14px', lineHeight: 1.5, marginBottom: '1.75rem' }}>
        Let's start with your name so we can personalise your experience.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1.5rem' }}>
        <TextInput
          id="onb-first-name"
          labelText="First name"
          placeholder="e.g. Rosa"
          value={firstName}
          onChange={(e) => onFirstNameChange(e.target.value)}
          required
        />
        <TextInput
          id="onb-last-name"
          labelText="Last name"
          placeholder="e.g. Martinez"
          value={lastName}
          onChange={(e) => onLastNameChange(e.target.value)}
          required
        />
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <Button disabled={disabled} onClick={onNext}>
          Next →
        </Button>
      </div>
    </>
  );
}

// ─── Step 2: Location ─────────────────────────────────────────────────────────

interface Step2Props {
  locationIndex: number; // 0-based
  draft: LocationDraft;
  onDraftChange: (draft: LocationDraft) => void;
  onBack: () => void;
  onNext: () => void;
  geocodeError: string | null;
  geocoding: boolean;
}

function Step2({
  locationIndex,
  draft,
  onDraftChange,
  onBack,
  onNext,
  geocodeError,
  geocoding,
}: Step2Props) {
  const locationLabel = locationIndex === 0 ? 'first' : `#${locationIndex + 1}`;
  const nextDisabled = !draft.locName.trim() || !draft.address.trim() || geocoding;

  return (
    <>
      <StepDots current={2} total={3} />
      <h1
        style={{
          fontSize: '1.75rem',
          fontWeight: 400,
          letterSpacing: '-0.02em',
          margin: '0 0 0.5rem',
        }}
      >
        Add your {locationLabel} location.
      </h1>
      <p style={{ color: '#525252', fontSize: '14px', lineHeight: 1.5, marginBottom: '1.75rem' }}>
        Runway uses this to personalise your outage plan. You can add more locations after finishing.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1.25rem' }}>
        <TextInput
          id="onb-loc-name"
          labelText="Location name"
          placeholder="e.g. Home"
          value={draft.locName}
          onChange={(e) => onDraftChange({ ...draft, locName: e.target.value })}
          required
        />

        <TextInput
          id="onb-loc-description"
          labelText={
            <>
              Description <span style={{ color: '#525252', fontWeight: 400 }}>(optional)</span>
            </>
          }
          placeholder="e.g. Main residence"
          value={draft.description}
          onChange={(e) => onDraftChange({ ...draft, description: e.target.value })}
        />

        <div>
          <TextInput
            id="onb-loc-address"
            labelText="Address"
            placeholder="e.g. 123 Main St, Los Angeles, CA"
            value={draft.address}
            onChange={(e) =>
              onDraftChange({ ...draft, address: e.target.value })
            }
            invalid={!!geocodeError}
            invalidText={geocodeError ?? undefined}
            required
          />
        </div>

        <DeviceSurvey
          items={draft.electronic_items}
          onChange={(items) => onDraftChange({ ...draft, electronic_items: items })}
        />

        <TextArea
          id="onb-loc-notes"
          labelText={
            <>
              Additional notes <span style={{ color: '#525252', fontWeight: 400 }}>(optional)</span>
            </>
          }
          placeholder="Anything else we should know about this location…"
          value={draft.additional_notes}
          onChange={(e) => onDraftChange({ ...draft, additional_notes: e.target.value })}
          rows={3}
        />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Button kind="tertiary" onClick={onBack} disabled={geocoding}>
          ← Back
        </Button>
        <Button disabled={nextDisabled} onClick={onNext}>
          {geocoding ? <InlineLoading description="Checking address…" /> : 'Next →'}
        </Button>
      </div>
    </>
  );
}

// ─── Step 3: Decision ─────────────────────────────────────────────────────────

interface Step3Props {
  locationCount: number;
  onAddAnother: () => void;
  onFinish: () => void;
  saving: boolean;
  saveError: string | null;
}

function Step3({ locationCount, onAddAnother, onFinish, saving, saveError }: Step3Props) {
  return (
    <>
      <StepDots current={3} total={3} />
      <h1
        style={{
          fontSize: '1.75rem',
          fontWeight: 400,
          letterSpacing: '-0.02em',
          margin: '0 0 0.5rem',
        }}
      >
        Add another location?
      </h1>
      <p style={{ color: '#525252', fontSize: '14px', lineHeight: 1.5, marginBottom: '1.75rem' }}>
        You have added{' '}
        <strong>{locationCount}</strong>{' '}
        {locationCount === 1 ? 'location' : 'locations'} so far.
        You can always add more from your profile settings.
      </p>

      {saveError && (
        <div style={{ marginBottom: '1rem' }}>
          <InlineNotification
            kind="error"
            title="Save failed"
            subtitle={saveError}
            lowContrast
            hideCloseButton
          />
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <Button
          kind="tertiary"
          onClick={onAddAnother}
          disabled={saving}
          style={{ width: '100%', maxWidth: '100%', justifyContent: 'center' }}
        >
          Yes, add another location
        </Button>
        <Button
          kind="primary"
          onClick={onFinish}
          disabled={saving}
          style={{ width: '100%', maxWidth: '100%', justifyContent: 'center' }}
        >
          {saving ? <InlineLoading description="Saving…" /> : 'No, finish setup'}
        </Button>
      </div>
    </>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function OnboardingPage() {
  const navigate = useNavigate();
  const { profile } = useAuth();

  // ── Redirect guard: profile already complete ──────────────────────────────
  useEffect(() => {
    if (
      profile &&
      profile.first_name &&
      profile.first_name.trim() !== '' &&
      profile.last_name &&
      profile.last_name.trim() !== ''
    ) {
      navigate('/', { replace: true });
    }
  }, [profile, navigate]);

  // ── Step + accumulated data ───────────────────────────────────────────────
  const [step, setStep] = useState<Step>(1);

  // Step 1
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');

  // Step 2
  const [locationDraft, setLocationDraft] = useState<LocationDraft>(emptyLocationDraft());
  const [geocoding, setGeocoding] = useState(false);
  const [geocodeError, setGeocodeError] = useState<string | null>(null);

  // Accumulated ready locations (between loops of Step 2 → 3 → 2)
  const [readyLocations, setReadyLocations] = useState<ReadyLocation[]>([]);

  // Step 3
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // ── Handlers ─────────────────────────────────────────────────────────────

  function handleStep1Next() {
    setStep(2);
  }

  async function handleStep2Next() {
    setGeocodeError(null);
    setGeocoding(true);
    try {
      const geo = await geocodeAddress(locationDraft.address);
      const ready: ReadyLocation = {
        name: locationDraft.locName.trim(),
        description: locationDraft.description.trim() || null,
        address: locationDraft.address.trim(),
        lat: geo.lat,
        lon: geo.lon,
        fips_code: geo.fips_code ?? null,
        electronic_items: locationDraft.electronic_items,
        additional_notes: locationDraft.additional_notes.trim() || null,
      };
      setReadyLocations((prev) => [...prev, ready]);
      setStep(3);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Address not found. Please check and try again.';
      setGeocodeError(message);
    } finally {
      setGeocoding(false);
    }
  }

  function handleAddAnother() {
    // Reset draft but keep step 2 visible with blank form
    setLocationDraft(emptyLocationDraft());
    setGeocodeError(null);
    setStep(2);
  }

  async function handleFinish() {
    setSaving(true);
    setSaveError(null);
    try {
      // 1. Save profile (name)
      const saved = await upsertProfile({ first_name: firstName.trim(), last_name: lastName.trim() });
      if (!saved) throw new Error('Failed to save your profile. Please try again.');

      // 2. Create all accumulated locations
      const created: Awaited<ReturnType<typeof createLocation>>[] = [];
      for (const loc of readyLocations) {
        const result = await createLocation(loc);
        created.push(result);
      }

      // 3. Set first location as default
      if (created.length > 0) {
        await setDefaultLocation(created[0].id);
      }

      // 4. Navigate home
      navigate('/', { replace: true });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'An unexpected error occurred. Please try again.';
      setSaveError(message);
    } finally {
      setSaving(false);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Theme theme="g10">
      <div className="site-shell">
        {/* Site header (matches rest of app) */}
        <header className="site-header">
          <div className="brand">
            <span className="brand-mark">
              <Flash size={20} />
            </span>
            <span>
              runway<span className="brand-period">.</span>
            </span>
          </div>
          <div className="header-right">
            <Link to="/model-sources" className="header-model-link">
              Model sources ↗
            </Link>
          </div>
        </header>

        {/* Centered card */}
        <main style={{ padding: '3rem 0 4rem' }}>
          <OnboardingCard>
            {step === 1 && (
              <Step1
                firstName={firstName}
                lastName={lastName}
                onFirstNameChange={setFirstName}
                onLastNameChange={setLastName}
                onNext={handleStep1Next}
              />
            )}

            {step === 2 && (
              <Step2
                locationIndex={readyLocations.length}
                draft={locationDraft}
                onDraftChange={setLocationDraft}
                onBack={() => {
                  setGeocodeError(null);
                  setStep(readyLocations.length === 0 ? 1 : 3);
                }}
                onNext={handleStep2Next}
                geocodeError={geocodeError}
                geocoding={geocoding}
              />
            )}

            {step === 3 && (
              <Step3
                locationCount={readyLocations.length}
                onAddAnother={handleAddAnother}
                onFinish={handleFinish}
                saving={saving}
                saveError={saveError}
              />
            )}
          </OnboardingCard>
        </main>

        <footer className="site-footer">
          <span>Runway · Static product preview</span>
        </footer>
      </div>
    </Theme>
  );
}
