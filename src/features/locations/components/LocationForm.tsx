// src/features/locations/components/LocationForm.tsx
// Carbon form for creating or editing a location.

import { useState } from 'react';
import {
  TextInput,
  TextArea,
  Button,
  InlineNotification,
  Stack,
} from '@carbon/react';
import { geocodeAddress } from '../../../lib/mapbox/geocode';
import { createLocation, updateLocation } from '../locationService';
import type { ElectronicItem, Location } from '../../../types/database';
import DeviceSurvey from '../../../components/DeviceSurvey';

export interface LocationFormProps {
  initialData?: Partial<Location>;
  onSave: (location: Location) => void;
  onCancel: () => void;
}

interface FormState {
  name: string;
  description: string;
  address: string;
  additional_notes: string;
}

export default function LocationForm({
  initialData,
  onSave,
  onCancel,
}: LocationFormProps) {
  const isEdit = Boolean(initialData?.id);

  const [form, setForm] = useState<FormState>({
    name: initialData?.name ?? '',
    description: initialData?.description ?? '',
    address: initialData?.address ?? '',
    additional_notes: initialData?.additional_notes ?? '',
  });

  const [electronicItems, setElectronicItems] = useState<ElectronicItem[]>(
    initialData?.electronic_items ?? [],
  );

  const [addressError, setAddressError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function handleChange(field: keyof FormState, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
    if (field === 'address') setAddressError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    setAddressError(null);

    // Basic required-field checks
    if (!form.name.trim()) return;
    if (!form.address.trim()) {
      setAddressError('Address is required.');
      return;
    }

    setSaving(true);
    let geocoded: { lat: number; lon: number; fips_code?: string };
    try {
      geocoded = await geocodeAddress(form.address.trim());
    } catch (err) {
      setAddressError(
        err instanceof Error
          ? err.message
          : 'Could not geocode address. Please check the address and try again.',
      );
      setSaving(false);
      return;
    }

    try {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        address: form.address.trim(),
        additional_notes: form.additional_notes.trim() || null,
        lat: geocoded.lat,
        lon: geocoded.lon,
        fips_code: geocoded.fips_code ?? null,
        electronic_items: electronicItems,
      };

      let saved: Location;
      if (isEdit && initialData?.id) {
        saved = await updateLocation(initialData.id, payload);
      } else {
        saved = await createLocation(payload);
      }
      onSave(saved);
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : 'Failed to save location.',
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <Stack gap={5}>
        {submitError && (
          <InlineNotification
            kind="error"
            title="Save failed"
            subtitle={submitError}
            hideCloseButton
            lowContrast
          />
        )}

        <TextInput
          id="loc-name"
          labelText="Location name *"
          placeholder={'e.g. "Home" or "Parents\' house"'}
          value={form.name}
          onChange={(e) => handleChange('name', e.target.value)}
          required
          invalid={form.name.trim() === '' && saving}
          invalidText="Name is required."
        />

        <TextArea
          id="loc-description"
          labelText="Description"
          placeholder="Optional description"
          value={form.description}
          onChange={(e) => handleChange('description', e.target.value)}
          rows={2}
        />

        <TextInput
          id="loc-address"
          labelText="Address *"
          placeholder="123 Main St, Los Angeles, CA 90001"
          value={form.address}
          onChange={(e) => handleChange('address', e.target.value)}
          required
          invalid={Boolean(addressError)}
          invalidText={addressError ?? ''}
        />

        <TextArea
          id="loc-notes"
          labelText="Additional notes"
          placeholder="Any extra details about this location"
          value={form.additional_notes}
          onChange={(e) => handleChange('additional_notes', e.target.value)}
          rows={2}
        />

        <DeviceSurvey items={electronicItems} onChange={setElectronicItems} />

        <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.75rem', paddingTop: '0.75rem', borderTop: '1px solid #e0e0e0' }}>
          <Button type="submit" disabled={saving}>
            {saving ? 'Saving…' : isEdit ? 'Save changes' : 'Add location'}
          </Button>
          <Button kind="secondary" type="button" onClick={onCancel} disabled={saving}>
            Cancel
          </Button>
        </div>
      </Stack>
    </form>
  );
}
