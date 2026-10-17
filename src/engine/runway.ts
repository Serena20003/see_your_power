/**
 * runway.ts — battery runway calculation (specs.md §8.2)
 */

export type RunwayStatus = "ok" | "low" | "critical" | "unreachable" | "helped";

export interface RunwayResult {
  hoursLeft: number | null;
  socPct: number;
  status: RunwayStatus;
}

const DEVICE_WATTS: Record<string, number> = {
  oxygen_concentrator: 300,
  cpap: 50,
  home_dialysis: 150,
  insulin_fridge: 60,
  power_wheelchair: 150,
  none: 0,
};

export function deviceWatts(
  devices: string[],
  overrideWatts?: number
): number {
  if (overrideWatts != null) return overrideWatts;
  return devices.reduce((sum, d) => sum + (DEVICE_WATTS[d] ?? 0), 0);
}

export function computeRunway(
  batteryWh: number,
  socPct: number,
  watts: number,
  powerOut: boolean,
  isSilent: boolean
): RunwayResult {
  if (!powerOut) return { hoursLeft: null, socPct, status: "ok" };
  if (isSilent) return { hoursLeft: null, socPct, status: "unreachable" };
  if (watts === 0) return { hoursLeft: null, socPct, status: "ok" };

  const whLeft = batteryWh * (socPct / 100);
  const hoursLeft = whLeft / watts;

  let status: RunwayStatus;
  if (hoursLeft >= 6) status = "ok";
  else if (hoursLeft >= 2) status = "low";
  else status = "critical";

  return { hoursLeft, socPct, status };
}

/** Drain battery by one simulated minute (called each clock tick). */
export function drainBattery(
  batteryWh: number,
  socPct: number,
  watts: number
): number {
  if (watts === 0) return socPct;
  const drainPerMin = (watts / batteryWh) * 100 / 60;
  return Math.max(0, socPct - drainPerMin);
}
