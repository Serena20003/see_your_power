import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../lib/supabase/AuthContext";

/**
 * Wraps protected routes. Session is already resolved by AuthProvider (which
 * shows its own spinner). Unauthenticated users are redirected to /.
 */
export default function ProtectedRoute() {
  const { session } = useAuth();
  return session ? <Outlet /> : <Navigate to="/" replace />;
}
