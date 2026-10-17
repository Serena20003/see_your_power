/**
 * plan.ts — personal preparation plan (specs.md §8.5)
 *
 * Tries POST /api/plan (LLM server function) with a 5 s timeout.
 * Falls back to plan_templates.json for the resident's device types.
 */

import type { CountyForecast, DeviceType, Resident } from "../types";

// -------------------------------------------------------------------------
// Plan item type
// -------------------------------------------------------------------------

export type PlanWhen =
  | "two_days_before"
  | "night_before"
  | "one_hour_before"
  | "during_outage";

export interface PlanItem {
  id: string;
  title: string;
  title_en: string;
  when: PlanWhen;
  reason: string;
  reason_en: string;
  done: boolean;
}

// -------------------------------------------------------------------------
// API request / response schema (section 10)
// -------------------------------------------------------------------------

interface PlanRequest {
  resident: {
    name: string;
    age: number;
    devices: DeviceType[];
    batteryWh: number;
    isRenter: boolean;
    livesAlone: boolean;
    hasEV: boolean;
    language: string;
  };
  forecast: {
    probability: number;
    windowStart: string;
    windowEnd: string;
    durationLowHours: number;
    durationHighHours: number;
    primaryCause: string;
    reasons: string[];
  };
  runwayHoursAtFullCharge: number;
  nearestCoolPlace: string;
}

interface PlanApiResponse {
  items: Array<{
    title: string;
    title_en: string;
    when: PlanWhen;
    reason: string;
    reason_en: string;
  }>;
}

// -------------------------------------------------------------------------
// Template fallback (mirrors plan_templates.json structure)
// -------------------------------------------------------------------------

const TEMPLATES: Record<string, { title: string; when: PlanWhen; reason: string }[]> = {
  oxygen_concentrator: [
    {
      title: "Charge your oxygen battery to 100% tonight",
      when: "night_before",
      reason: "Your oxygen concentrator needs backup power during an outage.",
    },
    {
      title: "Ask your supplier about backup oxygen tanks",
      when: "two_days_before",
      reason: "A tank provides extra safety if the battery runs low.",
    },
    {
      title: "If your battery covers fewer hours than the outage, plan where to go",
      when: "night_before",
      reason: "Knowing your destination in advance reduces stress during an outage.",
    },
  ],
  cpap: [
    {
      title: "Charge your CPAP battery tonight",
      when: "night_before",
      reason: "A charged battery keeps your CPAP running if power goes out.",
    },
  ],
  home_dialysis: [
    {
      title: "Call your dialysis center to confirm a backup plan",
      when: "two_days_before",
      reason: "Your center needs to know about a possible power disruption.",
    },
  ],
  insulin_fridge: [
    {
      title: "Keep the fridge closed; food and medicine stay cold for about 4 hours",
      when: "during_outage",
      reason: "Opening the fridge lets cold air escape faster.",
    },
    {
      title: "Prepare a cooler with ice packs",
      when: "night_before",
      reason: "A cooler can keep insulin safe if the fridge warms up.",
    },
  ],
  power_wheelchair: [
    {
      title: "Charge your wheelchair fully tonight",
      when: "night_before",
      reason: "Full charge gives you maximum mobility if power is interrupted.",
    },
  ],
  general: [
    {
      title: "Charge your phone and a power bank",
      when: "night_before",
      reason: "A charged phone keeps you connected during an outage.",
    },
    {
      title: "Tell your contact about the forecast",
      when: "night_before",
      reason: "Letting someone know helps them check on you if needed.",
    },
    {
      title: "Keep a flashlight within reach",
      when: "night_before",
      reason: "A flashlight helps you move around safely if lights go out.",
    },
    {
      title: "Never use a generator or grill indoors",
      when: "during_outage",
      reason: "Indoor generators and grills produce carbon monoxide, which is dangerous.",
    },
  ],
};

function buildFallbackPlan(
  resident: Resident,
  coolPlace: string
): PlanItem[] {
  const items: PlanItem[] = [];
  let idx = 0;

  for (const device of resident.devices) {
    const templates = TEMPLATES[device] ?? [];
    for (const t of templates) {
      items.push({
        id: `plan-${idx++}`,
        title: t.title,
        title_en: t.title,
        when: t.when,
        reason: t.reason,
        reason_en: t.reason,
        done: false,
      });
    }
  }

  for (const t of TEMPLATES.general) {
    const title = t.title
      .replace("{contact}", resident.contacts[0]?.name ?? "your family")
      .replace("{coolPlace}", coolPlace);
    items.push({
      id: `plan-${idx++}`,
      title,
      title_en: title,
      when: t.when,
      reason: t.reason,
      reason_en: t.reason,
      done: false,
    });
  }

  return items;
}

// -------------------------------------------------------------------------
// Main function
// -------------------------------------------------------------------------

export async function buildPlan(
  resident: Resident,
  forecast: CountyForecast,
  runwayHoursAtFullCharge: number,
  nearestCoolPlace: string
): Promise<PlanItem[]> {
  const window = forecast.next_24h;

  // Skip API call if we're in offline mode or live data is off
  const useLive = Boolean(import.meta.env.VITE_API_BASE_URL);
  if (!useLive) {
    return buildFallbackPlan(resident, nearestCoolPlace);
  }

  const req: PlanRequest = {
    resident: {
      name: resident.name,
      age: resident.age,
      devices: resident.devices,
      batteryWh: resident.batteryWh,
      isRenter: resident.isRenter,
      livesAlone: resident.livesAlone,
      hasEV: resident.hasEV,
      language: resident.language,
    },
    forecast: {
      probability: window.probability ?? 0,
      windowStart: window.window_start,
      windowEnd: window.window_end,
      durationLowHours: window.duration_low_hours ?? 2,
      durationHighHours: window.duration_high_hours ?? 8,
      primaryCause: window.primary_driver,
      reasons: window.reasons,
    },
    runwayHoursAtFullCharge,
    nearestCoolPlace,
  };

  try {
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), 5_000);
    const resp = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
      signal: controller.signal,
    });
    clearTimeout(id);

    if (!resp.ok) throw new Error(`/api/plan returned ${resp.status}`);
    const data = (await resp.json()) as PlanApiResponse;

    if (!Array.isArray(data.items) || data.items.length < 4) {
      throw new Error("Invalid plan response");
    }

    return data.items.map((item, i) => ({
      id: `plan-${i}`,
      ...item,
      done: false,
    }));
  } catch {
    return buildFallbackPlan(resident, nearestCoolPlace);
  }
}
