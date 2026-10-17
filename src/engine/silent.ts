/**
 * silent.ts — why a battery stopped reporting (specs.md §8.3)
 */

export type SilentReason = "device_likely" | "network_likely";

const SILENT_THRESHOLD_MS = 10 * 60 * 1_000; // 10 simulated minutes

/** Returns true if the battery is silent (telemetry has stopped). */
export function isBatterySilent(
  telemetryLastSeen: Date,
  simTime: Date
): boolean {
  return simTime.getTime() - telemetryLastSeen.getTime() > SILENT_THRESHOLD_MS;
}

/**
 * Determine why a battery went silent, given all residents in the same
 * cell zone.
 *
 * @param cellZoneLastSeen  telemetryLastSeen times for all residents in the
 *                          same cellZoneId (including the current resident).
 * @param simTime           current simulation time
 */
export function silentReason(
  cellZoneLastSeen: Date[],
  simTime: Date
): SilentReason {
  if (cellZoneLastSeen.length === 0) return "device_likely";
  const silentCount = cellZoneLastSeen.filter((t) =>
    isBatterySilent(t, simTime)
  ).length;
  return silentCount / cellZoneLastSeen.length > 0.5
    ? "network_likely"
    : "device_likely";
}
