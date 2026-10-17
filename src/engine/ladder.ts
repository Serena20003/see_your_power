/**
 * ladder.ts — escalation rules (specs.md §8.4)
 *
 * Evaluated once per simulated minute for every resident with powerOut = true.
 * Steps 6 and 9 require explicit human approval ("never-automate" rule).
 */

import type { Resident } from "../types";

export type LadderStep =
  | "none"
  | "watching"
  | "texted_resident"
  | "called_resident"
  | "notified_family"
  | "notified_neighbor"
  | "delivery_requested"
  | "delivery_approved"
  | "delivery_completed"
  | "wellness_requested"
  | "wellness_approved"
  | "resident_ok";

export interface MessageEvent {
  id: string;
  simTime: Date;
  step: LadderStep;
  to: string;       // recipient description
  text: string;
  requiresApproval: boolean;
}

export interface LadderState {
  step: LadderStep;
  okPressedAt: Date | null;
  pausedUntil: Date | null;
  familyConfirmed: boolean;
  pendingApproval: boolean;
  deliveryDispatchedAt: Date | null;
  wellnessDispatchedAt: Date | null;
  events: MessageEvent[];
}

export function initialLadderState(): LadderState {
  return {
    step: "none",
    okPressedAt: null,
    pausedUntil: null,
    familyConfirmed: false,
    pendingApproval: false,
    deliveryDispatchedAt: null,
    wellnessDispatchedAt: null,
    events: [],
  };
}

// Seeded pseudo-random so scenarios replay identically
function seededRand(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) & 0xffffffff;
    return (s >>> 0) / 4294967296;
  };
}

function addMins(d: Date, m: number): Date {
  return new Date(d.getTime() + m * 60_000);
}

/**
 * Advance the escalation ladder for one resident.
 *
 * @returns  updated LadderState (immutable — caller replaces the old one)
 */
export function tickLadder(
  state: LadderState,
  resident: Resident,
  hoursLeft: number | null,
  simTime: Date,
  coolPlace: string,
  silentDeviceLikely: boolean,
  rand: () => number
): LadderState {
  let s = { ...state, events: [...state.events] };

  function emit(step: LadderStep, to: string, text: string, requiresApproval = false) {
    s.events.push({
      id: `${resident.id}-${step}-${simTime.getTime()}`,
      simTime,
      step,
      to,
      text,
      requiresApproval,
    });
    s.step = step;
  }

  const h = hoursLeft ?? 999;

  // Pause check (resident pressed "I'm OK")
  if (s.pausedUntil && simTime < s.pausedUntil) return s;
  if (s.pausedUntil && simTime >= s.pausedUntil && h < 6) {
    s.pausedUntil = null;
    // Resume from step texted_resident
  }

  // Step 1 — power just went out
  if (s.step === "none") {
    const hh = Math.floor(h);
    const mm = Math.round((h - hh) * 60);
    const device = resident.devices[0] ?? "device";
    emit(
      "watching",
      `Resident (${resident.name})`,
      `Your power is out. Your ${device} battery has about ${hh}h ${mm}m left. Reply OK if you are safe.`
    );
    return s;
  }

  // Step 2 — hoursLeft < 6
  if (s.step === "watching" && h < 6) {
    emit(
      "texted_resident",
      `Resident (${resident.name})`,
      `Your battery has about ${Math.round(h)} hours left. Nearest place with power: ${coolPlace}.`
    );
    // Simulate answer
    if (rand() < resident.answerProbability) {
      s.step = "resident_ok";
      s.okPressedAt = simTime;
      s.pausedUntil = addMins(simTime, 120);
      return s;
    }
    return s;
  }

  // Step 3 — hoursLeft < 3
  if (s.step === "texted_resident" && h < 3) {
    // device_likely silent: skip straight to called_resident
    emit(
      "called_resident",
      `Resident (${resident.name})`,
      `This is Runway checking on you. Press 1 if you are OK.`
    );
    if (rand() < resident.answerProbability) {
      s.step = "resident_ok";
      s.okPressedAt = simTime;
      s.pausedUntil = addMins(simTime, 120);
      return s;
    }
    return s;
  }

  // Step 4 — 10 min after step 3, no answer
  const lastCallEvent = s.events.find((e) => e.step === "called_resident");
  if (
    s.step === "called_resident" &&
    lastCallEvent &&
    simTime >= addMins(lastCallEvent.simTime, 10)
  ) {
    const contact = resident.contacts.find((c) => c.relation === "family");
    if (contact) {
      emit(
        "notified_family",
        `Family (${contact.name})`,
        `We could not reach ${resident.name}. Their battery has about ${Math.round(h)} hours left. Please check on them.`
      );
      if (rand() < contact.answerProbability) {
        s.familyConfirmed = true;
      }
    }
    return s;
  }

  // Step 5 — 10 min after step 4, no family confirmation
  const familyEvent = s.events.find((e) => e.step === "notified_family");
  if (
    s.step === "notified_family" &&
    !s.familyConfirmed &&
    familyEvent &&
    simTime >= addMins(familyEvent.simTime, 10)
  ) {
    const neighbor = resident.contacts.find((c) => c.relation === "neighbor");
    if (neighbor) {
      emit(
        "notified_neighbor",
        `Neighbor (${neighbor.name})`,
        `Can you check on ${resident.name}? Their medical device battery is running low.`
      );
    }
    return s;
  }

  // Step 6 — hoursLeft < 2 (requires approval)
  if (
    (s.step === "notified_family" || s.step === "notified_neighbor") &&
    h < 2 &&
    !s.pendingApproval
  ) {
    const hh = Math.floor(h);
    const mm = Math.round((h - hh) * 60);
    emit(
      "delivery_requested",
      "County operator",
      `Request a fresh battery for ${resident.name}, ${hh}h ${mm}m left.`,
      true // requiresApproval
    );
    s.pendingApproval = true;
    return s;
  }

  // Step 9 — hoursLeft < 1, no contact (requires approval)
  if (h < 1 && s.step !== "wellness_requested" && s.step !== "wellness_approved") {
    emit(
      "wellness_requested",
      "County operator",
      `Request a wellness check for ${resident.name}.`,
      true
    );
    s.pendingApproval = true;
    return s;
  }

  return s;
}

/**
 * Approve a pending step (delivery_requested or wellness_requested).
 * Steps 7 and 10 from the spec.
 */
export function approveLadderStep(
  state: LadderState,
  resident: Resident,
  simTime: Date
): LadderState {
  const s = { ...state, events: [...state.events], pendingApproval: false };

  if (state.step === "delivery_requested") {
    s.step = "delivery_approved";
    s.deliveryDispatchedAt = simTime;
    s.events.push({
      id: `${resident.id}-delivery_approved-${simTime.getTime()}`,
      simTime,
      step: "delivery_approved",
      to: `Resident (${resident.name})`,
      text: "A fresh battery is on its way. Expected in about 45 minutes.",
      requiresApproval: false,
    });
  } else if (state.step === "wellness_requested") {
    s.step = "wellness_approved";
    s.wellnessDispatchedAt = simTime;
    s.events.push({
      id: `${resident.id}-wellness_approved-${simTime.getTime()}`,
      simTime,
      step: "wellness_approved",
      to: "County",
      text: `Wellness check dispatched for ${resident.name}.`,
      requiresApproval: false,
    });
  }
  return s;
}
