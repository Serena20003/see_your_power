// src/pages/ProfilePage.tsx
// Profile settings page — IBM-style sidebar nav + sectioned content.

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Theme, InlineNotification, InlineLoading, Tag } from '@carbon/react';
import { Flash, Edit, TrashCan } from '@carbon/icons-react';
import { useAuth } from '../lib/supabase/AuthContext';
import ProfileForm from '../features/profile/components/ProfileForm';
import LocationForm from '../features/locations/components/LocationForm';
import { useLocations } from '../features/locations/useLocations';
import { deleteLocation, setDefaultLocation } from '../features/locations/locationService';
import type { Location, UserProfile } from '../types/database';

type View =
  | { mode: 'list' }
  | { mode: 'add' }
  | { mode: 'edit'; location: Location };

// ─── Sidebar nav items ──────────────────────────────────────────────────────
const NAV_ITEMS = [
  { id: 'contact',   label: 'Contact information' },
  { id: 'locations', label: 'Your locations'       },
  { id: 'account',   label: 'Account'              },
];

export default function ProfilePage() {
  const { session, profile, signOut } = useAuth();
  const navigate = useNavigate();
  const { locations, defaultLocation, loading, error, refetch } = useLocations();

  const [view, setView] = useState<View>({ mode: 'list' });
  const [actionError, setActionError] = useState<string | null>(null);
  const [activeNav, setActiveNav] = useState('contact');
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  async function handleSignOut() {
    await signOut();
    navigate('/');
  }

  function handleProfileSaved(_updated: UserProfile) { /* AuthContext refreshes on auth state change */ }

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

  function handleSaved(_loc: Location) {
    setView({ mode: 'list' });
    void refetch();
  }

  const initials =
    profile?.first_name && profile?.last_name
      ? `${profile.first_name[0]}${profile.last_name[0]}`.toUpperCase()
      : session?.user?.email?.[0]?.toUpperCase() ?? '?';

  const fullName = [profile?.first_name, profile?.last_name].filter(Boolean).join(' ') || '—';

  return (
    <Theme theme="g10">
      <div className="site-shell">
        {/* ── Header ── */}
        <header className="site-header">
          <div className="brand">
            <span className="brand-mark"><Flash size={20} /></span>
            <span>runway<span className="brand-period">.</span></span>
          </div>
          <div className="header-right">
            <Button kind="ghost" size="sm" onClick={() => navigate('/')}>
              ← Back to app
            </Button>
            <span className="avatar" aria-hidden="true">{initials}</span>
          </div>
        </header>

        {/* ── Page body: sidebar + content ── */}
        <div className="profile-page-layout">

          {/* ── Sidebar ── */}
          <aside className="profile-sidebar">
            <h1 className="profile-sidebar-title">Profile</h1>
            <nav aria-label="Profile sections">
              <ul className="profile-nav-list">
                {NAV_ITEMS.map((item) => (
                  <li key={item.id}>
                    <a
                      href={`#profile-${item.id}`}
                      className={`profile-nav-link${activeNav === item.id ? ' active' : ''}`}
                      onClick={() => setActiveNav(item.id)}
                    >
                      {item.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>
          </aside>

          {/* ── Main content ── */}
          <main className="profile-content">

            {/* ── Contact information ─────────────────────────────────── */}
            <section id="profile-contact" className="profile-section">
              <h2 className="profile-section-title">Contact information</h2>

              <div className="profile-row">
                <span className="profile-row-label">Name</span>
                <span className="profile-row-value">{fullName}</span>
              </div>

              <div className="profile-row">
                <span className="profile-row-label">Email address</span>
                <span className="profile-row-value">
                  {session?.user?.email ?? '—'}
                  {session?.user?.email && (
                    <Tag type="blue" size="sm" style={{ marginLeft: '0.5rem' }}>Primary</Tag>
                  )}
                </span>
              </div>

              {/* ── Edit name inline ── */}
              <div className="profile-edit-block">
                <h3 className="profile-edit-title">Edit name</h3>
                <ProfileForm profile={profile} onSaved={handleProfileSaved} />
              </div>
            </section>

            {/* ── Locations ───────────────────────────────────────────── */}
            <section id="profile-locations" className="profile-section">
              <h2 className="profile-section-title">Your locations</h2>

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

              {/* Add / Edit form */}
              {view.mode !== 'list' && (
                <div className="profile-edit-block">
                  <h3 className="profile-edit-title">
                    {view.mode === 'add'
                      ? 'Add new location'
                      : `Edit: ${(view as { mode: 'edit'; location: Location }).location.name}`}
                  </h3>
                  <LocationForm
                    initialData={view.mode === 'edit' ? view.location : undefined}
                    onSave={handleSaved}
                    onCancel={() => setView({ mode: 'list' })}
                  />
                </div>
              )}

              {/* Locations list as rows */}
              {view.mode === 'list' && (
                <>
                  {loading ? (
                    <InlineLoading description="Loading locations…" />
                  ) : error ? (
                    <InlineNotification kind="error" title="Could not load locations" subtitle={error} hideCloseButton lowContrast />
                  ) : locations.length === 0 ? (
                    <p className="profile-row-empty">No locations yet. Add your first location below.</p>
                  ) : (
                    locations.map((loc) => {
                      const isDefault = loc.id === defaultLocation?.id;
                      const awaitingDelete = pendingDeleteId === loc.id;
                      const deviceCount = loc.electronic_items?.length ?? 0;
                      return (
                        <div key={loc.id} className="profile-row profile-row--location">
                          <div className="profile-row-label">
                            <span className="profile-loc-name">{loc.name}</span>
                            {isDefault && <Tag type="blue" size="sm">DEFAULT</Tag>}
                          </div>
                          <div className="profile-row-value profile-loc-meta">
                            {loc.address && <span>{loc.address}</span>}
                            <span style={{ color: '#6f6f6f', fontSize: '0.8rem' }}>
                              {deviceCount === 0 ? 'No devices' : `${deviceCount} device type${deviceCount === 1 ? '' : 's'}`}
                            </span>
                            {!isDefault && !awaitingDelete && (
                              <button
                                type="button"
                                className="profile-inline-link"
                                onClick={() => handleSetDefault(loc)}
                              >
                                Set as default
                              </button>
                            )}
                            {awaitingDelete && (
                              <div className="profile-delete-confirm">
                                <span>Delete this location?</span>
                                <Button kind="danger" size="sm" onClick={() => { setPendingDeleteId(null); handleDelete(loc); }}>Confirm</Button>
                                <Button kind="secondary" size="sm" onClick={() => setPendingDeleteId(null)}>Cancel</Button>
                              </div>
                            )}
                          </div>
                          <div className="profile-row-actions">
                            <Button kind="ghost" size="sm" renderIcon={Edit} iconDescription="Edit location" hasIconOnly tooltipPosition="left" onClick={() => { setPendingDeleteId(null); setView({ mode: 'edit', location: loc }); }} />
                            <Button kind="ghost" size="sm" renderIcon={TrashCan} iconDescription="Delete location" hasIconOnly tooltipPosition="left" onClick={() => setPendingDeleteId(awaitingDelete ? null : loc.id)} />
                          </div>
                        </div>
                      );
                    })
                  )}

                  <div style={{ marginTop: '1.25rem' }}>
                    <Button
                      kind="tertiary"
                      style={{ justifyContent: 'center', minHeight: '44px', paddingLeft: '1.25rem', paddingRight: '1.25rem' }}
                      onClick={() => setView({ mode: 'add' })}
                    >
                      + Add new location
                    </Button>
                  </div>
                </>
              )}
            </section>

            {/* ── Account ─────────────────────────────────────────────── */}
            <section id="profile-account" className="profile-section profile-section--last">
              <h2 className="profile-section-title">Account</h2>

              <div className="profile-row">
                <span className="profile-row-label">Sign out</span>
                <span className="profile-row-value" style={{ color: '#6f6f6f', fontSize: '0.875rem' }}>
                  You are signed in as {session?.user?.email ?? '—'}
                </span>
                <div className="profile-row-actions">
                  <Button
                    kind="danger--ghost"
                    size="sm"
                    onClick={handleSignOut}
                  >
                    Sign out
                  </Button>
                </div>
              </div>

            </section>

          </main>
        </div>
      </div>
    </Theme>
  );
}
