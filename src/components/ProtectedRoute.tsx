import { useEffect, useState } from "react";
import { Navigate, Outlet } from "react-router-dom";
import { supabase } from "../lib/supabase/client";
import type { Session } from "@supabase/supabase-js";

/**
 * Wraps protected routes. While the Supabase session is being resolved, a
 * full-screen loading spinner is shown. Once resolved, unauthenticated users
 * are redirected to / and authenticated users see the child route.
 */
export default function ProtectedRoute() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
  }, []);

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
        }}
      >
        <span>Loading…</span>
      </div>
    );
  }

  return session ? <Outlet /> : <Navigate to="/" replace />;
}
