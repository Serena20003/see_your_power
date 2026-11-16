// src/pages/ProfilePage.tsx
// Profile settings page — user info + locations management.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Tile, Theme, InlineNotification, InlineLoading } from '@carbon/react';
import { Flash } from '@carbon/icons-react';
import { useAuth } from '../lib/supabase/AuthContext';
import ProfileForm from '../features/profile/components/ProfileForm';
import LocationForm from '../features/locations/components/LocationForm';
import LocationsList from '../features/locations/components/LocationsList';
import { useLocations } from '../features/locations/useLocations';
import { deleteLocation, setDefaultLocation } from '../features/locations/locationService';
import type { Location, UserProfile } from '../types/database';

type View =
  | { mode: 'list' }
  | { mode: 'add' }
  | { mode: 'edit'; location: Location };

export default function ProfilePage() {
  const { session, profile, signOut } = useAuth();
  const navigate = useNavigate();
  const { locations, defaultLocation, loading, error, refetch } = useLocations();

  const [view, setView] = useState<View>({ mode: 'list' });
  const [actionError, setActionError] = useState<string | null>(null);

  async function handleSignOut() {
    await signOut();
    navigate('/');
  }

  function handleProfileSaved(updated: UserProfile) {
    // AuthContext will update on the next auth state change; ProfileForm shows its own success notification.
    void updated;
  }

  async function handleDelete(loc: Location) {
    setActionError(null);
    try {
      await deleteLocation(loc.id);
      await refetch();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to delete location.');
    }
  }

  async function handleSetDefault(loc: Location) {
    setActionError(null);
    try {
      await setDefaultLocation(loc.id);
      await refetch();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to set default location.');
    }
  }

  function handleSaved(loc: Location) {
    void loc;
    setView({ mode: 'list' });
    void refetch();
  }

  return (
    <Theme theme="g10">
      <div className="site-shell">
        <header className="site-header">
          <div className="brand">
            <span className="brand-mark"><Flash size={20} /></span>
            <span>runway<span className="brand-period">.</span></span>
          </div>
          <div className="header-right">
            <Button kind="ghost" size="sm" onClick={() => navigate('/')}>
              ← Back to app
            </Button>
          </div>
        </header>

        <main style={{ maxWidth: '640px', margin: '0 auto', padding: '2rem 1rem' }}>
          <h1 style={{ marginBottom: '2rem' }}>Profile settings</h1>

          {/* ── User info ─────────────────────────────────────────────── */}
          <Tile style={{ marginBottom: '2rem' }}>
            <h2 style={{ marginBottom: '1.25rem' }}>Your info</h2>

            {session?.user?.email && (
              <p style={{ marginBottom: '1.25rem', color: '#57606a' }}>
                <strong>Email</strong><br />
                {session.user.email}
              </p>
            )}

            <ProfileForm profile={profile} onSaved={handleProfileSaved} />
          </Tile>

          {/* ── Locations ─────────────────────────────────────────────── */}
          <Tile style={{ marginBottom: '2rem' }}>
            <h2 style={{ marginBottom: '1rem' }}>Your locations</h2>

            {actionError && (
              <InlineNotification
                kind="error"
                title="Error"
                subtitle={actionError}
                hideCloseButton
                lowContrast
                style={{ marginBottom: '1rem' }}
                onCloseButtonClick={() => setActionError(null)}
              />
            )}

            {/* ── Add / Edit form ──────────────────────────────────── */}
            {view.mode !== 'list' && (
              <div style={{ marginBottom: '1.5rem' }}>
                <h3 style={{ marginBottom: '1rem' }}>
                  {view.mode === 'add' ? 'Add new location' : `Edit: ${(view as { mode: 'edit'; location: Location }).location.name}`}
                </h3>
                <LocationForm
                  initialData={view.mode === 'edit' ? view.location : undefined}
                  onSave={handleSaved}
                  onCancel={() => setView({ mode: 'list' })}
                />
              </div>
            )}

            {/* ── List ────────────────────────────────────────────── */}
            {view.mode === 'list' && (
              <>
                {loading ? (
                  <InlineLoading description="Loading locations…" />
                ) : error ? (
                  <InlineNotification
                    kind="error"
                    title="Could not load locations"
                    subtitle={error}
                    hideCloseButton
                    lowContrast
                  />
                ) : (
                  <LocationsList
                    locations={locations}
                    defaultLocationId={defaultLocation?.id ?? null}
                    onEdit={(loc) => setView({ mode: 'edit', location: loc })}
                    onDelete={handleDelete}
                    onSetDefault={handleSetDefault}
                  />
                )}

                <Button
                  kind="tertiary"
                  size="sm"
                  style={{ marginTop: '1rem' }}
                  onClick={() => setView({ mode: 'add' })}
                >
                  + Add new location
                </Button>
              </>
            )}
          </Tile>

          {/* ── Sign out ──────────────────────────────────────────────── */}
          <Button kind="danger--ghost" onClick={handleSignOut}>
            Sign out
          </Button>
        </main>
      </div>
    </Theme>
  );
}
