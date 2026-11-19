-- 003_delete_account.sql
-- Adds a SECURITY DEFINER RPC that lets a signed-in user delete their own
-- account from auth.users, which cascades to public.users and public.locations
-- via the ON DELETE CASCADE foreign keys defined in 001_initial_schema.sql.
--
-- Called from the client as: supabase.rpc('delete_current_user')
-- The anon key cannot touch auth.users directly, so this function runs as the
-- postgres role (SECURITY DEFINER) to perform the privileged deletion.

CREATE OR REPLACE FUNCTION public.delete_current_user()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  _uid uuid := auth.uid();
BEGIN
  -- Guard: only allow authenticated callers.
  IF _uid IS NULL THEN
    RAISE EXCEPTION 'Not authenticated';
  END IF;

  -- Delete the row from auth.users.
  -- Cascade rules (defined in 001_initial_schema.sql):
  --   auth.users → public.users  (ON DELETE CASCADE)
  --   auth.users → public.locations  (ON DELETE CASCADE)
  -- All public-schema user data is therefore removed in the same transaction.
  DELETE FROM auth.users WHERE id = _uid;
END;
$$;

-- Only authenticated users may call this function.
REVOKE ALL ON FUNCTION public.delete_current_user() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.delete_current_user() TO authenticated;
