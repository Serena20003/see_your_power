// src/features/locations/components/LocationsList.tsx
// Renders a list of location cards with edit, delete, and set-default actions.

import { useState } from 'react';
import { Button, Tag } from '@carbon/react';
import { Edit, TrashCan } from '@carbon/icons-react';
import type { Location } from '../../../types/database';

export interface LocationsListProps {
  locations: Location[];
  defaultLocationId: string | null;
  onEdit: (loc: Location) => void;
  onDelete: (loc: Location) => void;
  onSetDefault: (loc: Location) => void;
}

export default function LocationsList({
  locations,
  defaultLocationId,
  onEdit,
  onDelete,
  onSetDefault,
}: LocationsListProps) {
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  if (locations.length === 0) {
    return (
      <p style={{ color: '#57606a', marginTop: '0.5rem' }}>
        No locations yet. Add your first location above.
      </p>
    );
  }

  return (
    <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
      {locations.map((loc) => {
        const isDefault = loc.id === defaultLocationId;
        const awaitingDelete = pendingDeleteId === loc.id;
        const deviceCount = loc.electronic_items?.length ?? 0;

        return (
          <li
            key={loc.id}
            style={{
              border: '1px solid #e5e7eb',
              borderRadius: '4px',
              padding: '1rem',
              marginBottom: '0.75rem',
              background: '#f7f8fa',
            }}
          >
            {/* ── Card header ─────────────────────────────────────────── */}
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'flex-start',
                gap: '0.5rem',
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                  <strong style={{ fontSize: '0.95rem' }}>{loc.name}</strong>
                  {isDefault && (
                    <Tag type="blue" size="sm">
                      DEFAULT
                    </Tag>
                  )}
                </div>
                {loc.address && (
                  <p style={{ margin: '0.25rem 0 0', color: '#57606a', fontSize: '0.85rem' }}>
                    {loc.address}
                  </p>
                )}
                <p style={{ margin: '0.25rem 0 0', color: '#57606a', fontSize: '0.8rem' }}>
                  {deviceCount === 0
                    ? 'No devices'
                    : `${deviceCount} device type${deviceCount === 1 ? '' : 's'}`}
                </p>
              </div>

              {/* ── Action buttons ──────────────────────────────────── */}
              <div style={{ display: 'flex', gap: '0.25rem', flexShrink: 0 }}>
                <Button
                  kind="ghost"
                  size="sm"
                  iconDescription="Edit location"
                  renderIcon={Edit}
                  hasIconOnly
                  onClick={() => {
                    setPendingDeleteId(null);
                    onEdit(loc);
                  }}
                  tooltipPosition="left"
                />
                <Button
                  kind="ghost"
                  size="sm"
                  iconDescription="Delete location"
                  renderIcon={TrashCan}
                  hasIconOnly
                  onClick={() => setPendingDeleteId(awaitingDelete ? null : loc.id)}
                  tooltipPosition="left"
                />
              </div>
            </div>

            {/* ── Set as default link ──────────────────────────────────── */}
            {!isDefault && !awaitingDelete && (
              <button
                type="button"
                onClick={() => onSetDefault(loc)}
                style={{
                  marginTop: '0.5rem',
                  background: 'none',
                  border: 'none',
                  padding: 0,
                  color: '#3b82d4',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  textDecoration: 'underline',
                }}
              >
                Set as default
              </button>
            )}

            {/* ── Inline delete confirmation ───────────────────────────── */}
            {awaitingDelete && (
              <div
                style={{
                  marginTop: '0.75rem',
                  padding: '0.75rem',
                  background: '#fff',
                  border: '1px solid #e5e7eb',
                  borderRadius: '4px',
                }}
              >
                <p style={{ margin: '0 0 0.5rem', fontSize: '0.875rem', color: '#1f2328' }}>
                  Delete this location?
                </p>
                <div style={{ display: 'flex', gap: '0.5rem' }}>
                  <Button
                    kind="danger"
                    size="sm"
                    onClick={() => {
                      setPendingDeleteId(null);
                      onDelete(loc);
                    }}
                  >
                    Confirm
                  </Button>
                  <Button
                    kind="secondary"
                    size="sm"
                    onClick={() => setPendingDeleteId(null)}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
