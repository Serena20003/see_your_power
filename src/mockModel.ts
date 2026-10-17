export type ProfileKey = "oxygen" | "insulin" | "ev" | "laundry" | "food" | "family" | "costAware";
export type Profile = Record<ProfileKey, boolean>;
export type PlanItem = { id: string; when: string; title: string; why: string; category: "essential" | "home" | "savings" };

export const initialProfile: Profile = { oxygen: true, insulin: false, ev: false, laundry: true, food: true, family: true, costAware: true };
export const profileChoices: { key: ProfileKey; title: string; detail: string }[] = [
  { key: "oxygen", title: "Powered medical device", detail: "Example: oxygen concentrator" },
  { key: "insulin", title: "Temperature-sensitive medicine", detail: "Example: insulin stored in a fridge" },
  { key: "ev", title: "Electric vehicle", detail: "Include charging suggestions" },
  { key: "laundry", title: "Flexible laundry time", detail: "Suggest a convenient time" },
  { key: "food", title: "Food in fridge or freezer", detail: "Include food preparation" },
  { key: "family", title: "Family check-ins", detail: "Example contact: Elena" },
  { key: "costAware", title: "Lower electricity costs", detail: "Use sample lower-price windows" },
];

// In production this stays a separate client-side plan layer. The forecast model
// supplies area conditions; household preferences supply the personal actions.
export function createPlan(profile: Profile, probability: number): PlanItem[] {
  const higherRisk = probability >= 50;
  const prepTime = higherRisk ? "BEFORE FORECAST WINDOW" : "WHEN CONVENIENT";
  const items: PlanItem[] = [];
  if (profile.oxygen) items.push({ id: "oxygen", when: prepTime, title: "Charge your device backup battery", why: "This profile includes a powered medical device. Backup charge gives you more time if electricity is interrupted.", category: "essential" });
  if (profile.insulin) items.push({ id: "insulin", when: "THIS WEEK", title: "Review your medicine cooling plan", why: "This profile includes temperature-sensitive medicine. A power interruption can affect refrigeration; follow its storage instructions and ask your pharmacist about your specific supply.", category: "essential" });
  if (profile.family) items.push({ id: "family", when: prepTime, title: higherRisk ? "Set a check-in time with Elena" : "Keep your check-in plan current", why: "Family check-ins are included in this profile. Agreeing on a time and backup way to reach each other makes the next step clear if service is interrupted.", category: "essential" });
  if (profile.ev) items.push({ id: "ev", when: profile.costAware ? "SAMPLE LOWER-PRICE WINDOW · AFTER 9 PM" : prepTime, title: profile.costAware ? "Charge your EV in a lower-price window" : "Charge your EV before the forecast window", why: profile.costAware ? "This profile includes an EV and a lower-cost preference. After 9 PM is only a sample rate window; check your real utility plan before acting." : "This profile includes an EV, so having enough charge may give you more options during an outage.", category: profile.costAware ? "savings" : "home" });
  if (profile.laundry) items.push({ id: "laundry", when: profile.costAware ? "SAMPLE LOWER-PRICE WINDOW · AFTER 9 PM" : prepTime, title: profile.costAware ? "Move laundry to a lower-price time" : "Finish laundry when it works for you", why: profile.costAware ? "This profile includes flexible laundry and a lower-cost preference. This sample price window needs confirmation against your real tariff." : "Laundry timing is marked as flexible, so it can move away from the forecast window.", category: profile.costAware ? "savings" : "home" });
  if (profile.food) items.push({ id: "food", when: higherRisk ? prepTime : "ANYTIME", title: "Make a simple fridge and food plan", why: "Food storage is marked as important in this profile. A power interruption may affect your fridge or freezer.", category: "home" });
  items.push({ id: "phone", when: prepTime, title: "Charge your phone and power bank", why: "A charged phone helps you reach people and read your saved plan if household power goes out.", category: "home" });
  return items;
}
