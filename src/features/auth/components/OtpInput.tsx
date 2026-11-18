// src/features/auth/components/OtpInput.tsx
// Step 2 of the login flow: 6-digit OTP verification with resend and inline errors.

import { useState } from 'react';
import { Button, TextInput, InlineNotification } from '@carbon/react';
import type { AuthResponse } from '@supabase/supabase-js';
import { sendOtp, verifyOtp } from '../authService';

interface Props {
  email: string;
  /** Called with the verifyOtp response on successful verification. */
  onVerified: (result: AuthResponse) => void;
}

export default function OtpInput({ email, onVerified }: Props) {
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [resendState, setResendState] = useState<'idle' | 'sending' | 'sent'>('idle');

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (token.trim().length !== 6) {
      setError('Please enter the 6-digit code sent to your email.');
      return;
    }

    setLoading(true);
    try {
      const result = await verifyOtp(email, token);
      if (result.error) {
        setError(result.error.message);
        return;
      }
      onVerified(result);
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    setError(null);
    setToken('');
    setResendState('sending');
    try {
      const { error: resendError } = await sendOtp(email);
      if (resendError) {
        setError(resendError.message);
        setResendState('idle');
      } else {
        setResendState('sent');
      }
    } catch {
      setError('Failed to resend code. Please try again.');
      setResendState('idle');
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <p style={{ marginBottom: '1rem', color: '#525252', fontSize: '0.875rem' }}>
        We sent a 6-digit code to <strong>{email}</strong>. Enter it below.
      </p>

      <TextInput
        id="otp-token"
        type="text"
        labelText="Verification code"
        placeholder="123456"
        value={token}
        onChange={(e) => {
          // Allow only digits, max 6 characters
          const val = e.target.value.replace(/\D/g, '').slice(0, 6);
          setToken(val);
          if (error) setError(null);
          if (resendState === 'sent') setResendState('idle');
        }}
        invalid={!!error}
        invalidText={error ?? undefined}
        disabled={loading}
        autoComplete="one-time-code"
        inputMode="numeric"
        autoFocus
      />

      {error && (
        <InlineNotification
          kind="error"
          title="Error"
          subtitle={error}
          lowContrast
          hideCloseButton
          style={{ marginTop: '0.5rem' }}
        />
      )}

      {resendState === 'sent' && (
        <InlineNotification
          kind="success"
          title="Code resent"
          subtitle="A new code has been sent to your email."
          lowContrast
          hideCloseButton
          style={{ marginTop: '0.5rem' }}
        />
      )}

      <Button
        type="submit"
        disabled={loading || token.trim().length !== 6}
        style={{ marginTop: '1rem', width: '100%' }}
      >
        {loading ? 'Verifying…' : 'Verify code'}
      </Button>

      <div style={{ marginTop: '0.75rem', textAlign: 'center' }}>
        <button
          type="button"
          onClick={handleResend}
          disabled={resendState === 'sending'}
          style={{
            background: 'none',
            border: 'none',
            color: '#0f62fe',
            cursor: 'pointer',
            fontSize: '0.875rem',
            padding: 0,
            textDecoration: 'underline',
          }}
        >
          {resendState === 'sending' ? 'Sending…' : 'Resend code'}
        </button>
      </div>
    </form>
  );
}
