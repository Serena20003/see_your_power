// src/pages/LoginPage.tsx
// Two-step email OTP login/signup page.
// Step 1 — EmailForm: validate + send OTP
// Step 2 — OtpInput: verify code → redirect based on profile completeness

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading } from '@carbon/react';
import { supabase } from '../lib/supabase/client';
import { checkProfileComplete } from '../features/auth/authService';
import EmailForm from '../features/auth/components/EmailForm';
import OtpInput from '../features/auth/components/OtpInput';
import type { AuthResponse } from '@supabase/supabase-js';

type Step = 'email' | 'otp' | 'redirecting';

export default function LoginPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>('email');
  const [email, setEmail] = useState('');

  // If the user already has an active session, send them straight to the app.
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) navigate('/');
    });
  }, [navigate]);

  function handleOtpSent(sentEmail: string) {
    setEmail(sentEmail);
    setStep('otp');
  }

  async function handleVerified(result: AuthResponse) {
    const userId = result.data?.user?.id;
    if (!userId) {
      // Should not happen — verifyOtp succeeded, so user must exist.
      navigate('/');
      return;
    }

    setStep('redirecting');
    const isComplete = await checkProfileComplete(userId);
    navigate(isComplete ? '/' : '/onboarding');
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
        <p style={subheadStyle}>
          {step === 'email'
            ? 'Enter your email to receive a one-time sign-in code.'
            : 'Enter the code we sent to your inbox.'}
        </p>

        {step === 'email' && <EmailForm onOtpSent={handleOtpSent} />}

        {step === 'otp' && (
          <OtpInput email={email} onVerified={handleVerified} />
        )}

        {step === 'otp' && (
          <button
            type="button"
            onClick={() => setStep('email')}
            style={backLinkStyle}
          >
            ← Use a different email
          </button>
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
  padding: '2rem 1rem',
};

const cardStyle: React.CSSProperties = {
  background: '#ffffff',
  padding: '2rem',
  width: '100%',
  maxWidth: '400px',
  boxShadow: '0 2px 6px rgba(0,0,0,0.1)',
};

const containerStyle: React.CSSProperties = {
  minHeight: '100vh',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
};

const headingStyle: React.CSSProperties = {
  fontSize: '1.5rem',
  fontWeight: 600,
  marginBottom: '0.5rem',
};

const subheadStyle: React.CSSProperties = {
  color: '#525252',
  fontSize: '0.875rem',
  marginBottom: '1.5rem',
};

const backLinkStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: '#525252',
  cursor: 'pointer',
  fontSize: '0.8125rem',
  marginTop: '1rem',
  padding: 0,
  textDecoration: 'underline',
};
