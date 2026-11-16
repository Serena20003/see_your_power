// src/features/auth/authService.ts
// Auth helpers that wrap Supabase auth calls and Abstract email validation.

import { supabase } from '../../lib/supabase/client';

// ─── Email validation ─────────────────────────────────────────────────────────

/** Result returned by validateEmail(). */
export interface EmailValidationResult {
  valid: boolean;
  reason?: string;
}

/**
 * Validates an email address.
 * 1. Client-side regex format check.
 * 2. Abstract API deliverability + disposable check.
 * Falls back to format-only result if the Abstract API is unreachable.
 */
export async function validateEmail(email: string): Promise<EmailValidationResult> {
  // 1. Format check
  const formatOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  if (!formatOk) {
    return { valid: false, reason: 'Please enter a valid email address.' };
  }

  // 2. Abstract API check
  const apiKey = import.meta.env.VITE_ABSTRACT_EMAIL_API_KEY as string | undefined;
  if (!apiKey) {
    // Key not configured — fall back to format-only
    return { valid: true };
  }

  try {
    const url = `https://emailvalidation.abstractapi.com/v1/?api_key=${encodeURIComponent(apiKey)}&email=${encodeURIComponent(email.trim())}`;
    const res = await fetch(url);
    if (!res.ok) {
      // API error — fall back gracefully
      return { valid: true };
    }
    const data = (await res.json()) as {
      deliverability?: string;
      is_disposable_email?: { value: boolean };
    };
    if (data.is_disposable_email?.value) {
      return { valid: false, reason: 'Disposable email addresses are not allowed.' };
    }
    if (data.deliverability === 'UNDELIVERABLE') {
      return { valid: false, reason: 'This email address does not appear to be deliverable.' };
    }
    return { valid: true };
  } catch {
    // Network failure — fall back to format-only so the user is not blocked
    return { valid: true };
  }
}

// ─── Supabase OTP ─────────────────────────────────────────────────────────────

/** Sends a magic-link sign-in email via Supabase. */
export async function sendOtp(email: string) {
  return supabase.auth.signInWithOtp({
    email: email.trim(),
    options: {
      shouldCreateUser: true,
      emailRedirectTo: window.location.origin,
    },
  });
}

/** Verifies the 6-digit OTP code entered by the user. */
export async function verifyOtp(email: string, token: string) {
  return supabase.auth.verifyOtp({ email: email.trim(), token: token.trim(), type: 'email' });
}

// ─── Profile completeness ─────────────────────────────────────────────────────

/**
 * Returns true if the user's public.users row exists and has a non-empty first_name.
 * Used after OTP verify to decide whether to redirect to /onboarding or /.
 */
export async function checkProfileComplete(userId: string): Promise<boolean> {
  const { data } = await supabase
    .from('users')
    .select('first_name')
    .eq('id', userId)
    .maybeSingle<{ first_name: string | null }>();
  return !!(data?.first_name && data.first_name.trim().length > 0);
}

// ─── Sign out ─────────────────────────────────────────────────────────────────

/** Signs the current user out. */
export async function signOut() {
  return supabase.auth.signOut();
}
