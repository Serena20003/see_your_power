import { createClient } from '@supabase/supabase-js';
import type { Database } from '../../types/database';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error(
    'Missing Supabase environment variables. ' +
      'Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in .env.development.local.',
  );
}

/** Typed Supabase client singleton — import this everywhere instead of calling createClient() directly. */
export const supabase = createClient<Database>(supabaseUrl, supabaseAnonKey, {
  auth: {
    // Use implicit flow (hash tokens) instead of PKCE.
    // PKCE issues a one-time server-side code that gets consumed by email
    // client link-prefetches (Outlook SafeLinks, Gmail proxy) or React
    // StrictMode's double-invoke before the user ever clicks — causing the
    // "Sign-in link expired" error on the first login attempt.
    flowType: 'implicit',
  },
});
