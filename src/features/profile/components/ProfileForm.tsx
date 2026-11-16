// src/features/profile/components/ProfileForm.tsx
// Editable first name + last name form with save confirmation.

import { useState } from 'react';
import { Button, InlineNotification, TextInput } from '@carbon/react';
import { upsertProfile } from '../profileService';
import type { UserProfile } from '../../../types/database';

interface ProfileFormProps {
  profile: UserProfile | null;
  /** Called after a successful save so the parent can refresh the profile. */
  onSaved?: (updated: UserProfile) => void;
}

export default function ProfileForm({ profile, onSaved }: ProfileFormProps) {
  const [firstName, setFirstName] = useState(profile?.first_name ?? '');
  const [lastName, setLastName] = useState(profile?.last_name ?? '');
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<'idle' | 'success' | 'error'>('idle');
  const [errorMsg, setErrorMsg] = useState('');

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!firstName.trim() || !lastName.trim()) return;

    setSaving(true);
    setStatus('idle');

    const updated = await upsertProfile({
      first_name: firstName.trim(),
      last_name: lastName.trim(),
    });

    setSaving(false);

    if (updated) {
      setStatus('success');
      onSaved?.(updated);
    } else {
      setStatus('error');
      setErrorMsg('Could not save your profile. Please try again.');
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <TextInput
        id="profile-first-name"
        labelText="First name"
        value={firstName}
        onChange={(e) => { setFirstName(e.target.value); setStatus('idle'); }}
        required
        disabled={saving}
        autoComplete="given-name"
      />
      <TextInput
        id="profile-last-name"
        labelText="Last name"
        value={lastName}
        onChange={(e) => { setLastName(e.target.value); setStatus('idle'); }}
        required
        disabled={saving}
        autoComplete="family-name"
      />
      <Button type="submit" disabled={saving || !firstName.trim() || !lastName.trim()}>
        {saving ? 'Saving…' : 'Save'}
      </Button>

      {status === 'success' && (
        <InlineNotification
          kind="success"
          title="Saved"
          subtitle="Your profile has been updated."
          lowContrast
          onClose={() => setStatus('idle')}
        />
      )}
      {status === 'error' && (
        <InlineNotification
          kind="error"
          title="Error"
          subtitle={errorMsg}
          lowContrast
          onClose={() => setStatus('idle')}
        />
      )}
    </form>
  );
}
