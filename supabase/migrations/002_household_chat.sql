-- Persist plans beside each resident-owned location. Existing location RLS applies.
ALTER TABLE public.locations
  ADD COLUMN IF NOT EXISTS plan_items jsonb DEFAULT NULL,
  ADD COLUMN IF NOT EXISTS assistant_revision bigint NOT NULL DEFAULT 0;

ALTER TABLE public.locations
  ADD CONSTRAINT locations_plan_items_array
  CHECK (plan_items IS NULL OR jsonb_typeof(plan_items) = 'array');

-- Every location edit, including the existing profile form, invalidates stale chat writes.
CREATE OR REPLACE FUNCTION public.bump_household_revision()
RETURNS trigger LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  NEW.assistant_revision := OLD.assistant_revision + 1;
  RETURN NEW;
END;
$$;

CREATE TRIGGER locations_household_revision
BEFORE UPDATE ON public.locations
FOR EACH ROW EXECUTE FUNCTION public.bump_household_revision();
