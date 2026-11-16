// src/lib/supabase/AuthContext.tsx
// React context providing session, profile, and signOut to the entire component tree.

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import type { Session } from '@supabase/supabase-js';
import { InlineLoading } from '@carbon/react';
import { supabase } from './client';
import { signOut as authSignOut } from '../../features/auth/authService';
import { getProfile } from '../../features/profile/profileService';
import type { UserProfile } from '../../types/database';

// ─── Context shape ────────────────────────────────────────────────────────────

interface AuthContextValue {
  session: Session | null;
  profile: UserProfile | null;
  loading: boolean;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

// ─── Provider ─────────────────────────────────────────────────────────────────

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  /** Fetch profile whenever we have a valid session. */
  async function loadProfile(currentSession: Session | null) {
    if (!currentSession) {
      setProfile(null);
      return;
    }
    const data = await getProfile();
    setProfile(data);
  }

  useEffect(() => {
    // Resolve the initial session on mount
    supabase.auth.getSession().then(async ({ data: { session: initialSession } }) => {
      setSession(initialSession);
      await loadProfile(initialSession);
      setLoading(false);
    });

    // Subscribe to future session changes
    const { data: { subscription } } = supabase.auth.onAuthStateChange(async (_event, updatedSession) => {
      setSession(updatedSession);
      await loadProfile(updatedSession);
    });

    return () => { subscription.unsubscribe(); };
  }, []);

  async function signOut() {
    await authSignOut();
    setSession(null);
    setProfile(null);
  }

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <InlineLoading description="Loading…" />
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ session, profile, loading, signOut }}>
      {children}
    </AuthContext.Provider>
  );
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

/** Returns the current auth context. Must be used inside <AuthProvider>. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}
