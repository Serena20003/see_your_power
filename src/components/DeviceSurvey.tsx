// src/components/DeviceSurvey.tsx
// Controlled component for managing electronic devices at a location.
// Used in LocationForm (Sub-Task 5), OnboardingPage (Sub-Task 7), and
// ProfilePage (Sub-Task 10).

import { useState } from 'react';
import { Button, Tag, TextInput } from '@carbon/react';
import { Edit, TrashCan } from '@carbon/icons-react';
import type { ElectronicItem } from '../types/database';

// ─── Quick-add suggestion chips ───────────────────────────────────────────────
// The 7 original profile choices, presented as suggestion chips.
const SUGGESTION_CHIPS: string[] = [
  'Oxygen concentrator',
  'Insulin fridge',
  'Electric vehicle',
  'CPAP machine',
  'Power wheelchair',
  'Home dialysis',
  'Flexible laundry',
];

// ─── Props ────────────────────────────────────────────────────────────────────

export interface DeviceSurveyProps {
  items: ElectronicItem[];
  onChange: (items: ElectronicItem[]) => void;
}

// ─── Inline row state ─────────────────────────────────────────────────────────

type RowMode =
  | { type: 'closed' }
  | { type: 'add'; name: string; qty: string }
  | { type: 'edit'; index: number; name: string; qty: string };

// ─── Component ────────────────────────────────────────────────────────────────

export default function DeviceSurvey({ items, onChange }: DeviceSurveyProps) {
  const [row, setRow] = useState<RowMode>({ type: 'closed' });

  // ── Chip click ───────────────────────────────────────────────────────────
  function handleChipClick(chip: string) {
    // If we're already editing an existing row, don't override it with a chip
    if (row.type === 'edit') return;
    setRow({ type: 'add', name: chip, qty: '1' });
  }

  // ── Open blank add row ───────────────────────────────────────────────────
  function handleAddClick() {
    setRow({ type: 'add', name: '', qty: '1' });
  }

  // ── Confirm add ──────────────────────────────────────────────────────────
  function handleConfirmAdd() {
    if (row.type !== 'add') return;
    const name = row.name.trim();
    const qty = parseInt(row.qty, 10);
    if (!name || isNaN(qty) || qty < 1) return;
    onChange([...items, { name, number_owned: qty }]);
    setRow({ type: 'closed' });
  }

  // ── Open edit row ────────────────────────────────────────────────────────
  function handleEditClick(index: number) {
    const item = items[index];
    setRow({ type: 'edit', index, name: item.name, qty: String(item.number_owned) });
  }

  // ── Confirm edit ─────────────────────────────────────────────────────────
  function handleConfirmEdit() {
    if (row.type !== 'edit') return;
    const name = row.name.trim();
    const qty = parseInt(row.qty, 10);
    if (!name || isNaN(qty) || qty < 1) return;
    const updated = items.map((item, i) =>
      i === row.index ? { name, number_owned: qty } : item,
    );
    onChange(updated);
    setRow({ type: 'closed' });
  }

  // ── Delete ───────────────────────────────────────────────────────────────
  function handleDelete(index: number) {
    // If we're editing that exact row, close the row too
    if (row.type === 'edit' && row.index === index) {
      setRow({ type: 'closed' });
    }
    onChange(items.filter((_, i) => i !== index));
  }

  // ── Cancel ───────────────────────────────────────────────────────────────
  function handleCancel() {
    setRow({ type: 'closed' });
  }

  const isRowOpen = row.type !== 'closed';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {/* ── Section label ──────────────────────────────────────────────────── */}
      <p style={{ margin: 0, fontWeight: 600, fontSize: '0.875rem' }}>
        Electronic devices
      </p>

      {/* ── Quick-add suggestion chips ─────────────────────────────────────── */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        {SUGGESTION_CHIPS.map((chip) => (
          <Tag
            key={chip}
            type="blue"
            onClick={() => handleChipClick(chip)}
            style={{ cursor: row.type === 'edit' ? 'not-allowed' : 'pointer' }}
            title={row.type === 'edit' ? 'Finish editing before adding' : `Add ${chip}`}
          >
            {chip}
          </Tag>
        ))}
      </div>

      {/* ── Populated device list ──────────────────────────────────────────── */}
      {items.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {items.map((item, index) => {
            const isEditingThis = row.type === 'edit' && row.index === index;

            if (isEditingThis && row.type === 'edit') {
              return (
                <li key={index}>
                  <InlineRow
                    name={row.name}
                    qty={row.qty}
                    onNameChange={(v) => setRow({ ...row, name: v })}
                    onQtyChange={(v) => setRow({ ...row, qty: v })}
                    onConfirm={handleConfirmEdit}
                    onCancel={handleCancel}
                  />
                </li>
              );
            }

            return (
              <li
                key={index}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  padding: '0.4rem 0.75rem',
                  background: '#f4f4f4',
                  borderRadius: '4px',
                }}
              >
                <span style={{ flex: 1, fontSize: '0.875rem' }}>{item.name}</span>
                <span
                  style={{
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    background: '#e0e0e0',
                    borderRadius: '12px',
                    padding: '0.1rem 0.5rem',
                    minWidth: '1.5rem',
                    textAlign: 'center',
                  }}
                >
                  {item.number_owned}
                </span>
                <Button
                  kind="ghost"
                  size="sm"
                  iconDescription="Edit device"
                  renderIcon={Edit}
                  hasIconOnly
                  onClick={() => handleEditClick(index)}
                  disabled={isRowOpen && !isEditingThis}
                />
                <Button
                  kind="ghost"
                  size="sm"
                  iconDescription="Delete device"
                  renderIcon={TrashCan}
                  hasIconOnly
                  onClick={() => handleDelete(index)}
                />
              </li>
            );
          })}
        </ul>
      )}

      {/* ── Inline add row ─────────────────────────────────────────────────── */}
      {row.type === 'add' && (
        <InlineRow
          name={row.name}
          qty={row.qty}
          onNameChange={(v) => setRow({ ...row, name: v })}
          onQtyChange={(v) => setRow({ ...row, qty: v })}
          onConfirm={handleConfirmAdd}
          onCancel={handleCancel}
        />
      )}

      {/* ── Add device button ──────────────────────────────────────────────── */}
      <div>
        <Button
          kind="tertiary"
          size="sm"
          onClick={handleAddClick}
          disabled={isRowOpen}
        >
          + Add device
        </Button>
      </div>
    </div>
  );
}

// ─── Shared inline row (add + edit) ──────────────────────────────────────────

interface InlineRowProps {
  name: string;
  qty: string;
  onNameChange: (v: string) => void;
  onQtyChange: (v: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

function InlineRow({ name, qty, onNameChange, onQtyChange, onConfirm, onCancel }: InlineRowProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'flex-end',
        gap: '0.5rem',
        padding: '0.75rem',
        background: '#f4f4f4',
        borderRadius: '4px',
        border: '1px solid #e0e0e0',
      }}
    >
      <div style={{ flex: '1 1 180px' }}>
        <TextInput
          id="device-name-input"
          labelText="Device name"
          placeholder="e.g. CPAP machine"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          size="sm"
        />
      </div>
      <div style={{ flex: '0 0 80px' }}>
        <TextInput
          id="device-qty-input"
          labelText="Qty"
          type="number"
          value={qty}
          onChange={(e) => onQtyChange(e.target.value)}
          min={1}
          size="sm"
        />
      </div>
      <Button
        kind="primary"
        size="sm"
        onClick={onConfirm}
        disabled={!name.trim() || parseInt(qty, 10) < 1 || isNaN(parseInt(qty, 10))}
      >
        Confirm
      </Button>
      <Button kind="secondary" size="sm" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}
