// src/pages/ProfilePage.tsx
// Profile settings page — user info section.
// Locations section is a placeholder; full implementation comes in Sub-Task 10.

import { useNavigate } from 'react-router-dom';
import { Button, Tile, Theme } from '@carbon/react';
import { Flash } from '@carbon/icons-react';
import { useAuth } from '../lib/supabase/AuthContext';
import ProfileForm from '../features/profile/components/ProfileForm';
import type { UserProfile } from '../types/database';

export default function ProfilePage() {
  const { session, profile, signOut } = useAuth();
  const navigate = useNavigate();

  async function handleSignOut() {
    await signOut();
    navigate('/');
  }

  function handleProfileSaved(updated: UserProfile) {
    // AuthContext will update on the next auth state change; for immediate UI
    // feedback the ProfileForm shows its own success notification.
    void updated;
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

          {/* ── User info ──────────────────────────────────────────────── */}
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

          {/* ── Locations placeholder ──────────────────────────────────── */}
          <Tile style={{ marginBottom: '2rem' }}>
            <h2 style={{ marginBottom: '0.75rem' }}>Your locations</h2>
            <p style={{ color: '#57606a' }}>
              Locations will appear here — coming in a later update.
            </p>
          </Tile>

          {/* ── Sign out ───────────────────────────────────────────────── */}
          <Button kind="danger--ghost" onClick={handleSignOut}>
            Sign out
          </Button>
        </main>
      </div>
    </Theme>
  );
}
