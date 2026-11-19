// src/pages/LoginPage.tsx
// Two-step email magic-link login/signup page.
// Step 1 — EmailForm: validate + send magic link
// Step 2 — "check your inbox" confirmation; Supabase redirects back and
//           onAuthStateChange fires, which triggers navigation here.
//
// Error handling: Supabase appends #error=...&error_description=... to the
// redirectTo URL on failure (e.g. expired link). App.tsx detects this hash
// and navigates to /login?error=<description> so we can surface it here.

import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Loading, InlineNotification } from '@carbon/react';
import { supabase } from '../lib/supabase/client';
import { checkProfileComplete } from '../features/auth/authService';
import EmailForm from '../features/auth/components/EmailForm';

type Step = 'email' | 'sent' | 'redirecting';

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');

  // Error forwarded from the Supabase redirect hash (via App.tsx → navigate).
  const linkError = searchParams.get('error');

  // Handle both: existing session on mount AND the magic-link redirect
  // (which fires onAuthStateChange with SIGNED_IN).
  useEffect(() => {
    // Check for an already-active session (e.g. user revisits while logged in).
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (session) {
        setStep('redirecting');
        const isComplete = await checkProfileComplete(session.user.id);
        navigate(isComplete ? '/' : '/onboarding');
      }
    });

    // Listen for the SIGNED_IN event that fires when the magic link is clicked.
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        if (event === 'SIGNED_IN' && session) {
          setStep('redirecting');
          const isComplete = await checkProfileComplete(session.user.id);
          navigate(isComplete ? '/' : '/onboarding');
        }
      },
    );

    return () => { subscription.unsubscribe(); };
  }, [navigate]);

  function handleLinkSent(sentEmail: string) {
    setEmail(sentEmail);
    setStep('sent');
  }

  if (step === 'redirecting') {
    return (
      <div style={containerStyle}>
        <Loading description="Signing you in…" withOverlay={false} small />
      </div>
    );
  }

  return (
    <div style={pageStyle}>
      <div style={cardStyle}>
        <h1 style={headingStyle}>Log in / Sign up</h1>

        {linkError && (
          <InlineNotification
            kind="error"
            title="Sign-in link expired"
            subtitle="That link has already been used or has expired. Request a new one below."
            lowContrast
            hideCloseButton
            style={{ marginBottom: '1rem' }}
          />
        )}

        {step === 'email' && (
          <>
            <p style={subheadStyle}>
              Enter your email to receive a one-time sign-in link.
            </p>
            <EmailForm onOtpSent={handleLinkSent} />
          </>
        )}

        {step === 'sent' && (
          <>
            <p style={subheadStyle}>
              We sent a sign-in link to <strong>{email}</strong>. Click the
              link in your inbox to continue — you can close this tab.
            </p>
            <button
              type="button"
              onClick={() => setStep('email')}
              style={backLinkStyle}
            >
              ← Use a different email
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Styles ────────────────────────────────────────────────────────────────────

const pageStyle: React.CSSProperties = {
  minHeight: '100vh',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  backgroundColor: '#f4f4f4',
  padding: '2rem 1.5rem',
};

const cardStyle: React.CSSProperties = {
  background: '#ffffff',
  padding: '3rem 3.5rem',
  width: '100%',
  maxWidth: '480px',
  boxShadow: '0 4px 16px rgba(0,0,0,0.10)',
};

const containerStyle: React.CSSProperties = {
  minHeight: '100vh',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
};

const headingStyle: React.CSSProperties = {
  fontSize: '1.75rem',
  fontWeight: 600,
  marginBottom: '0.5rem',
  letterSpacing: '-0.02em',
};

const subheadStyle: React.CSSProperties = {
  color: '#525252',
  fontSize: '0.9375rem',
  marginBottom: '2rem',
  lineHeight: 1.5,
};

const backLinkStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: '#525252',
  cursor: 'pointer',
  fontSize: '0.875rem',
  marginTop: '1.25rem',
  padding: 0,
  textDecoration: 'underline',
};
