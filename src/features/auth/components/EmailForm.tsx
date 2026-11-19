// src/features/auth/components/EmailForm.tsx
// Step 1 of the login flow: collect and validate the user's email, then send OTP.

import { useState } from 'react';
import { Button, TextInput, InlineNotification } from '@carbon/react';
import { validateEmail, sendOtp } from '../authService';

interface Props {
  /** Called when OTP has been sent successfully; parent advances to OTP step. */
  onOtpSent: (email: string) => void;
}

export default function EmailForm({ onOtpSent }: Props) {
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    // Client-side check first (fast, no network)
    if (!email.trim()) {
      setError('Please enter your email address.');
      return;
    }

    setLoading(true);
    try {
      // Abstract API + format validation
      const validation = await validateEmail(email);
      if (!validation.valid) {
        setError(validation.reason ?? 'Invalid email address.');
        return;
      }

      // Send OTP
      const { error: otpError } = await sendOtp(email);
      if (otpError) {
        setError(otpError.message);
        return;
      }

      onOtpSent(email.trim());
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <TextInput
        id="email"
        type="email"
        labelText="Email address"
        placeholder="you@example.com"
        value={email}
        onChange={(e) => {
          setEmail(e.target.value);
          if (error) setError(null);
        }}
        invalid={!!error}
        invalidText={error ?? undefined}
        disabled={loading}
        autoComplete="email"
        autoFocus
      />

      {/* Show error as inline notification too for accessibility when TextInput
          invalidText is not enough (e.g. API-level errors unrelated to the field) */}
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

      <Button
        type="submit"
        disabled={loading}
        style={{ marginTop: '1.5rem', width: '100%' }}
      >
        {loading ? 'Checking…' : 'Continue'}
      </Button>
    </form>
  );
}
