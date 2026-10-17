# Runway: Functional Build Spec

This document is a complete functional specification. It defines what the app does, the data it uses, the rules it follows and how to verify it works. It intentionally contains **no visual design**: layout, colors, fonts and styling are left to the developer and designer. Wherever this spec says "show", any reasonable UI is fine as long as the listed information and actions are present.

---

## 1. Product summary

Runway predicts new, unplanned power outages and why they may occur, gives people a personal preparation plan, and during an outage tracks how many hours their medical device's backup battery has left. It escalates help step by step before the battery runs out. A county or utility view shows everyone being watched, who runs out first, and the help in progress. Planned maintenance outages and public-safety power shutoffs (PSPS) are outside the prediction target and model inputs; they may be displayed separately as notices.

The model pilot covers all 50 U.S. states and DC at a county-level, 24-hour forecast horizon. The South Bay resident and scenarios below are an illustrative app demo, not a geographic restriction on the model. County probabilities require verified planned/unplanned labels and held-out validation; counties without adequate coverage show an insufficient-data state.

The app is a hackathon demo. It must:

- Run entirely in the browser from static JSON files, plus optional calls to free public APIs and two optional serverless functions.
- Never break during a 5-minute live demo. Every external call has an offline fallback.
- Label all synthetic data as "Sample data" wherever it appears.
- Use observed real-world data for every input used to fit, calibrate, validate, or run the outage prediction model. Synthetic residents, contacts, batteries, and simulated outage outcomes are allowed for the demo, but they are not model training or evaluation records.

---

## 2. Users and views

| View | Route | Who uses it | Device |
|---|---|---|---|
| Resident app | `/resident/:id` | A person at home (default demo person: `rosa`) | Phone-sized |
| Family view (inside messages feed) | shown on `/resident/:id` | Resident's family member | n/a |
| County view | `/county` | County emergency manager or utility staff | Laptop |
| Demo control bar | overlay on all routes, toggled with `Shift + D` | The presenting team | Laptop |

A combined route `/demo` shows the resident app, the messages feed and the county view side by side, all driven by the same simulation clock.

---

## 3. Core concepts

- **Resident:** a person being watched, with a home location, a medical device, a backup battery and contacts.
- **Neighborhood:** a small area with precomputed risk attributes (tree cover, overhead line share, fire zone, outage history).
- **Forecast:** a county-level unplanned-outage probability for a time window, shown to a resident in that county with appropriate geographic uncertainty, plus the top reasons.
- **Outage:** a period when a resident has no grid power. Their backup battery drains.
- **Runway:** hours of battery left = battery energy ÷ device power draw.
- **Ladder:** the sequence of escalating help actions, gentlest first.
- **Simulation clock:** one shared "current time" for the whole app, controlled by the demo bar. Everything is computed from it, never from the real clock.

---

## 4. Data model (TypeScript types)

```ts
// src/types.ts

export type DeviceType =
  | "oxygen_concentrator"
  | "cpap"
  | "home_dialysis"
  | "insulin_fridge"
  | "power_wheelchair"
  | "none";

export type FireZone = "none" | "tier2" | "tier3";

export interface Contact {
  name: string;
  relation: "family" | "neighbor" | "caregiver";
  phone: string;           // fake numbers in sample data
  answerProbability: number; // 0..1, used only in simulation
}

export interface Resident {
  id: string;              // "rosa" is the featured demo resident
  name: string;
  age: number;
  lat: number;
  lon: number;
  countyFips: string;       // county of the example resident; national forecast join key
  neighborhoodId: string;
  cellZoneId: string;      // groups residents on the same cell tower, for silent-battery logic
  devices: DeviceType[];
  deviceWattsOverride?: number; // if known, overrides the table in section 7
  batteryWh: number;       // usable backup battery capacity in watt-hours
  initialSocPct: number;   // battery charge % at the start of the scenario (0..100)
  hasEV: boolean;
  isRenter: boolean;
  livesAlone: boolean;
  language: "en" | "es" | "ko" | "tl" | "zh";
  phone: string;
  answerProbability: number; // 0..1, chance the resident answers a call in simulation
  contacts: Contact[];
  isSample: true;
}

export interface Neighborhood {
  id: string;
  countyFips: string;
  name: string;
  centroidLat: number;
  centroidLon: number;
  treeCoverPct: number;      // 0..100, from USGS tree canopy (precomputed)
  overheadLineShare: number; // 0..1, share of lines overhead from a documented real source
  fireZone?: FireZone;       // California demo layer only; not a common U.S. model input
  pastOutages3yr: number;    // count of verified unplanned outages in last 3 years
  medianOutageHours: number; // typical duration of past outages here
  nearestCoolPlace: { name: string; lat: number; lon: number; address: string };
}

export interface WeatherHour {
  time: string;          // ISO timestamp
  tempF: number;
  windGustMph: number;
  soilMoisture: number;  // m³/m³ from Open-Meteo, 0..~0.5
}

export interface WeatherAlert {
  event: string;         // e.g. "Red Flag Warning", "Heat Advisory", "Wind Advisory"
  onset: string;
  ends: string;
}

export interface Forecast {
  residentId: string;
  countyFips: string;             // model forecast geography
  probability: number;          // 0..1
  windowStart: string;          // ISO
  windowEnd: string;            // ISO
  durationLowHours: number;
  durationHighHours: number;
  primaryCause: "wind" | "heat" | "fire" | "other";
  reasons: ForecastReason[];    // top 4, sorted by contribution, highest first
  generatedAt: string;          // sim time
}

export interface ForecastReason {
  label: string;     // plain language, e.g. "Wind gusts up to 48 mph"
  contribution: number; // 0..1 relative weight, used for sorting
}

export interface PlanItem {
  id: string;
  title: string;          // e.g. "Charge your oxygen battery to 100% tonight"
  when: "two_days_before" | "night_before" | "one_hour_before" | "during_outage";
  reason: string;         // one short sentence
  done: boolean;
}

export type LadderStep =
  | "none"
  | "watching"
  | "texted_resident"
  | "called_resident"
  | "resident_ok"
  | "notified_family"
  | "notified_neighbor"
  | "delivery_requested"   // waiting for human approval
  | "delivery_approved"
  | "delivery_completed"
  | "wellness_requested"   // waiting for human approval
  | "wellness_approved";

export interface ResidentLiveState {
  residentId: string;
  powerOut: boolean;
  outageStartedAt?: string;
  restoreEstimate?: string;
  socPct: number;            // current battery %
  hoursLeft: number | null;  // null when unknown (silent battery)
  telemetryLastSeen: string; // last time the battery reported
  silentReason: "none" | "network_likely" | "device_likely";
  status: "ok" | "low" | "critical" | "unreachable" | "helped";
  ladderStep: LadderStep;
  okPressedAt?: string;
  lastContactAttemptAt?: string;
}

export interface MessageEvent {
  id: string;
  time: string;              // sim time
  residentId: string;
  channel: "sms" | "call" | "system";
  to: "resident" | "family" | "neighbor" | "county";
  text: string;
}

export interface ActionLogEntry {
  id: string;
  time: string;
  residentId: string;
  step: LadderStep;
  description: string;
  requiresApproval: boolean;
  approvedBy?: string;       // "County operator" in demo
  approvedAt?: string;
}

export interface Scenario {
  id: string;                // "heat_wave", "wildfire_unplanned", "twist_2", "twist_3"
  name: string;
  description: string;
  startTime: string;         // sim time at "2 days before"
  outageStart: string;       // sim time when outage begins
  outageEndEstimate: string;
  weather: WeatherHour[];    // used when live weather is off or fails
  alerts: WeatherAlert[];
  affectedNeighborhoodIds: string[]; // residents in these lose power at outageStart
  overrides?: Partial<Record<string, Partial<Resident>>>; // e.g. force Rosa not to answer
  silentBatteries?: { residentId: string; at: string; cause: "network" | "device" }[];
  extraRules?: string[];     // free-text notes for twists, shown in the demo bar
}
```

---

## 5. Static data files (in `public/data/`)

| File | Contents | How to create |
|---|---|---|
| `residents.json` | `Resident[]`, about 200 entries | Generated by `scripts/make_residents.py` (section 5.1) |
| `neighborhoods.json` | `Neighborhood[]`, 8 to 12 entries | Derived from documented real geographic and infrastructure sources; exclude unavailable features rather than inventing values |
| `scenarios/heat_wave.json` | `Scenario` | Demo events may be scripted; weather and alerts supplied to the prediction model must come from a documented real historical period |
| `scenarios/wildfire_unplanned.json` | `Scenario` | Demo events may be scripted; weather and alerts supplied to the prediction model must come from a documented real historical period |
| `scenarios/twist_2.json`, `twist_3.json` | `Scenario` | Created when each twist is announced |
| `device_watts.json` | map of `DeviceType` to typical watts | Section 7 |
| `plan_templates.json` | fallback plan items per device type | Section 10.3 |
| `fire_zones.geojson` | CPUC Tier 2/3 polygons clipped to the demo area | Downloaded, optional map layer |

### 5.1 Resident generator rules

- Bounding box for the South Bay demo area: latitude 33.75 to 33.93, longitude -118.42 to -118.28.
- Assign each resident to the nearest neighborhood centroid.
- Assign `cellZoneId` by splitting the box into 6 zones.
- Device mix: 35% oxygen concentrator, 25% CPAP, 10% home dialysis, 15% insulin fridge, 10% power wheelchair, 5% two devices.
- `batteryWh`: choose from 300, 500, 1000, 2000 Wh.
- `initialSocPct`: 60 to 100 for most; 15% of residents start between 20 and 50 (unprepared).
- `answerProbability`: 0.85 for most, 0.4 for 10%.
- 1 to 2 contacts each.
- Fixed featured resident:

```json
{
  "id": "rosa",
  "name": "Rosa Martinez",
  "age": 78,
  "devices": ["oxygen_concentrator"],
  "batteryWh": 1000,
  "initialSocPct": 100,
  "livesAlone": true,
  "language": "es",
  "answerProbability": 0,
  "contacts": [
    { "name": "Elena Martinez", "relation": "family", "phone": "+1-555-0101", "answerProbability": 1 },
    { "name": "Mr. Park", "relation": "neighbor", "phone": "+1-555-0102", "answerProbability": 1 }
  ],
  "isSample": true
}
```

Rosa's `answerProbability` is 0 so the demo always shows the call going unanswered and escalating to her daughter.

---

## 6. External data sources and APIs

All calls happen from the browser unless marked "server". Every call has an offline fallback. A global setting `liveData: boolean` (default `false` for demo safety) turns live calls on. Model inputs in scenario files are archived real observations or forecasts, with source, location, and timestamp recorded in a data manifest. Scripted outage timing and affected residents drive the simulation only; they are never used as evidence for the model's accuracy.

| Purpose | Source | Endpoint / access | Fallback |
|---|---|---|---|
| Hourly wind gusts, temperature, soil moisture | Open-Meteo | `GET https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,wind_gusts_10m,soil_moisture_0_to_1cm&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=auto&forecast_days=3` (free, no key) | `scenario.weather` |
| Official weather alerts | National Weather Service | `GET https://api.weather.gov/alerts/active?point={lat},{lon}` (free, no key) | `scenario.alerts` |
| California demo fire zones | CPUC High Fire-Threat District (via Cal OES / WIFIRE, GeoJSON) | Downloaded once into `fire_zones.geojson`; regional display only | Neighborhood `fireZone` field, never a common U.S. predictor |
| Tree cover | USGS tree canopy products | Precomputed by county into `treeCoverPct` with state-by-state coverage and vintage checks | If unavailable, omit the feature and flag coverage |
| Terrain | USGS 3DEP / National Map elevation | Precomputed by county from available consistent-resolution products | If unavailable, omit the feature and flag coverage |
| Overhead line share | Documented utility or public infrastructure inventory | Optional, separately validated regional enrichment only | Omit where unavailable; do not invent nationwide coverage |
| Unplanned outage history | Typed utility incident records or ODIN feeds with explicit outage type, aggregated by county | Precomputed into `pastOutages3yr`, `medianOutageHours`; exclude PSPS, maintenance, and unknown type | If unavailable, do not present a validated numeric probability |
| California outage example | Cal OES incidents marked `Not Planned` | Regional data-collection example, not national historical coverage | No national fallback |
| General outage context | ORNL EAGLE-I county counts | Reference only; published fields do not distinguish planned from unplanned outages | Never use as an unplanned-only label or history feature |
| Vulnerable population counts | HHS emPOWER Map | Used to size sample data only | n/a |
| Cooling / charging places | LA County cooling centers, OpenStreetMap | Precomputed into `nearestCoolPlace` | n/a |
| Personal plan text | Any LLM API (Claude, Gemini, IBM Granite on watsonx) | **Server**: `POST /api/plan` (section 10) | `plan_templates.json` |
| Automated phone call (stretch) | Retell AI | **Server**: `POST /api/call` (section 11) | Message shown in feed only |
| Real SMS (stretch) | Twilio | **Server**: `POST /api/sms` | Message shown in feed only |

When live weather is used, combine it with the scenario: live weather sets `WeatherHour[]`; the scenario still sets `outageStart`, affected neighborhoods and overrides.

### Prediction data rules

- The model source review includes 16 additional candidate sources with user-supplied priority ratings from 7.0 to 10.0. These ratings are evaluation priorities, not coefficients or percentages, and do not alter the provisional 100% feature allocation. CPUC and CAL FIRE remain California-only research inputs; emPOWER, SVI, ACS demographics, and roads primarily support response and equity planning. EAGLE-I, GDELT, and Bluesky cannot serve as typed unplanned-onset labels or leak active incidents into pre-event forecasts.
- Keep a machine-readable manifest for every model input and label: source URL or dataset identifier, collection date, geographic coverage, time range, units, transformation, and whether it is observed, forecast, or missing. Archived offline fallbacks must retain this provenance.
- Fit, tune, calibrate, and evaluate outage probabilities and duration estimates using real historical outage events matched to real weather and geographic features. Do not train or evaluate on generated residents, scripted scenario outcomes, invented outage records, or fabricated weather.
- The target is a **new unplanned outage onset** within the forecast window. Exclude PSPS, planned maintenance, and records with unknown outage type from training and evaluation labels. Never use PSPS notices or currently active target-area incidents as predictor inputs.
- ODIN accepts data from participating utilities, but outage detail and cause fields vary by provider. Build an explicit county-year label-coverage map before fitting a nationwide target. Cal OES is a California-only rolling current snapshot with an outage-type field, not a historical archive. EAGLE-I county totals cannot by themselves identify unplanned events.
- Check that outage labels have enough geographic and time detail for a county-level 24-hour onset target. A county forecast cannot establish whether a particular home lost power. If suitable labels cannot be obtained for a county, show an insufficient-data state rather than a numerical outage probability or claimed expected duration.
- Split historical data by time and geography so the same outage event cannot appear in both training and evaluation. Hold out states or utilities and report coverage, calibration, and performance by region, including Alaska and Hawaii, in the README. Never report performance measured on the scripted demo.
- Synthetic resident attributes may select a real neighborhood and real weather forecast for display. They must not alter the outage probability unless that resident-level feature has been supported and validated with real outcome data.

---

## 7. Device power table

`public/data/device_watts.json`. These are typical values for estimation; replace with manufacturer specs when known and show "estimated" next to any runway number derived from them.

```json
{
  "oxygen_concentrator": 300,
  "cpap": 50,
  "home_dialysis": 150,
  "insulin_fridge": 60,
  "power_wheelchair": 150,
  "none": 0
}
```

If a resident has several devices, sum their watts. If `deviceWattsOverride` is set, use it instead.

---

## 8. Engine (pure functions in `src/engine/`, all unit-tested)

### 8.1 `risk.ts`: illustrative South Bay demo forecast

Inputs: `Resident`, `Neighborhood`, `WeatherHour[]` for the next 24 hours, `WeatherAlert[]`, sim time. PSPS notices are not inputs.

The rules below are a transparent local-demo heuristic, not the nationwide predictor or a trained and calibrated probability model. The U.S. pilot instead uses county-level weather, nationally applicable physical features, and verified unplanned-outage history. Before showing demo output as a "% chance" or expected duration, replace or calibrate it with real historical unplanned-outage labels and evaluate it under section 6. No synthetic observations may be used for fitting, calibration, or evaluation. If this data gate is unmet, show a qualitative risk level and clearly label duration as an assumption. If a required real input is unavailable, show "Forecast unavailable" instead of substituting a fabricated value.

Steps:

1. `maxGust` = max `windGustMph` in the next 24 h. `maxTemp` = max `tempF`. `gustHour` and `tempHour` = when each max occurs.
2. `wetSoil` = 1 if max `soilMoisture` in the window > 0.30, else 0.
3. `windScore` = clamp((maxGust − 25) / 35, 0, 1) × (1 + 0.3 × wetSoil), then clamp to 1.
4. `heatScore` = clamp((maxTemp − 90) / 20, 0, 1).
5. `fireFactor` = 0 for `none`, 0.6 for `tier2`, 1.0 for `tier3`.
6. `exposure` = 0.4 × (treeCoverPct / 100) + 0.3 × overheadLineShare + 0.3 × fireFactor.
7. `history` = clamp(pastOutages3yr / 6, 0, 1), using only records classified as unplanned.
8. `hazard` = max(windScore, 0.8 × heatScore).
9. `p` = 1 − exp(−2.2 × hazard × (0.5 + exposure) × (0.6 + 0.8 × history)).
10. If an alert of type "Red Flag Warning" or "High Wind Warning" is active: `p` = max(p, 0.6). This is an illustrative hazard rule, not a learned probability.
11. Clamp `p` to 0.02..0.97.
12. `primaryCause`: `wind` if windScore ≥ 0.8 × heatScore, else `heat`.
13. Window: centered on `gustHour` (wind) or `tempHour` (heat), ±2 hours.
14. Duration:
    - wind: low = max(2, 0.5 × medianOutageHours), high = max(8, 1.5 × medianOutageHours)
    - heat: low 1, high 6 (illustrative equipment-failure range, requiring validation)
15. Reasons: build candidate reasons with contributions, keep the top 4 by contribution:
    - `Wind gusts up to {maxGust} mph` (windScore)
    - `Ground is wet, so trees fall more easily` (0.3 × wetSoil × windScore)
    - `Heat up to {maxTemp}°F will strain the grid` (heatScore)
    - `{treeCoverPct}% tree cover near power lines` (0.4 × treeCoverPct/100)
    - `Mostly overhead power lines` (0.3 × overheadLineShare)
    - `Tier 3 fire zone` or `Tier 2 fire zone` (0.3 × fireFactor)
    - `{pastOutages3yr} outages here in the last 3 years` (history)
    - `{alert.event} in effect` (0.8 per active alert)
16. Output a `Forecast`.

Display text rule: show probability as a whole percent, e.g. "74% chance", and the window as day + part of day, e.g. "Thursday afternoon".

### 8.2 `runway.ts`: hours of battery left

- `watts` = sum of device watts (section 7).
- `whLeft` = batteryWh × socPct / 100.
- `hoursLeft` = whLeft / watts (if watts is 0, return null).
- During an outage, each simulated minute reduces `socPct` by (watts / batteryWh) × 100 / 60.
- `socPct` never goes below 0. At 0, `hoursLeft` = 0 and status = `critical`.
- When a delivery is completed, `socPct` resets to 100 (a fresh battery).

Status rules:

| Condition | status |
|---|---|
| Power on | `ok` |
| Power out and hoursLeft ≥ 6 | `ok` |
| Power out and 2 ≤ hoursLeft < 6 | `low` |
| Power out and hoursLeft < 2 | `critical` |
| Silent battery and no contact in 10 min | `unreachable` |
| Delivery completed or resident pressed "I'm OK" after power restored | `helped` |

### 8.3 `silent.ts`: why a battery stopped reporting

- A battery is silent when sim time − `telemetryLastSeen` > 10 minutes.
- Look at all residents in the same `cellZoneId`. If more than 50% of them are also silent, set `silentReason` = `network_likely`; else `device_likely`.
- When silent: `hoursLeft` = null on the resident screen. The county view shows the last known runway estimate with the label "Estimated, battery not reporting".
- `device_likely` jumps the ladder straight to `called_resident` (skip `texted_resident`).
- `network_likely` follows the normal ladder using the last known estimate.

### 8.4 `ladder.ts`: escalation rules

Evaluated once per simulated minute for every resident with `powerOut = true`. Each rule fires once per outage. Every step adds a `MessageEvent` and an `ActionLogEntry`.

| Order | Trigger | Step | Message (resident language where possible; English shown here) | Needs approval |
|---|---|---|---|---|
| 1 | Power goes out | `watching` | Resident SMS: "Your power is out. Your {device} battery has about {h}h {m}m left. Reply OK if you are safe." | No |
| 2 | hoursLeft < 6 | `texted_resident` | Resident SMS: "Your battery has about {h} hours left. Nearest place with power: {coolPlace}." | No |
| 3 | hoursLeft < 3 | `called_resident` | Automated call to resident: "This is Runway checking on you. Press 1 if you are OK." | No |
| 4 | 10 sim minutes after step 3 with no answer | `notified_family` | Family SMS: "We could not reach {name}. Their {device} battery has about {h} hours left. Please check on them." | No |
| 5 | 10 sim minutes after step 4 with no family confirmation | `notified_neighbor` | Neighbor SMS: "Can you check on {name} at {address}? Their medical device battery is running low." | No |
| 6 | hoursLeft < 2 (or step 4/5 unanswered) | `delivery_requested` | County: "Request a fresh battery for {name}, {h}h {m}m left." | **Yes** |
| 7 | Operator approves step 6 | `delivery_approved` | Resident SMS: "A fresh battery is on its way. Expected in about 45 minutes." Family SMS: same. | n/a |
| 8 | 45 sim minutes after step 7 | `delivery_completed` | Resident SMS: "Your new battery has arrived." socPct resets to 100. | No |
| 9 | hoursLeft < 1 and no contact with anyone | `wellness_requested` | County: "Request a wellness check for {name}." | **Yes** |
| 10 | Operator approves step 9 | `wellness_approved` | County: "Wellness check dispatched for {name}." | n/a |

Answer simulation:

- When a call or SMS is sent, the recipient "answers" with their `answerProbability`. Use a seeded random generator so a scenario plays the same way every time.
- If the resident answers or presses "I'm OK": set `ladderStep` = `resident_ok`, set `okPressedAt`, and pause the ladder for 2 simulated hours. After 2 hours, resume from step 2 if hoursLeft is still under 6.
- If family answers: log "Family confirmed they are checking on {name}" and skip step 5. Step 6 still happens if hoursLeft < 2.

Never-automate rule: steps 6 and 9 must not proceed without a human click on Approve in the county view.

### 8.5 `plan.ts`: personal plan

- Call `POST /api/plan` with the resident profile and forecast (section 10).
- On error, timeout (5 s) or `liveData = false`, build the plan from `plan_templates.json` for the resident's devices plus general items.
- Always include at least: charge battery, phone charged, contact family, nearest cool place.
- Plan items are generated once per forecast and stored in state. Ticking an item sets `done = true`.

---

## 9. Simulation clock and scenarios

- One store value `simTime` (ISO string). All engine functions take it as input.
- When a scenario loads: `simTime` = `scenario.startTime` (2 days before the outage), all residents get `socPct = initialSocPct`, power on, ladder `none`.
- Clock modes: **paused** (default) and **play** (1 simulated minute per real 100 ms).
- On each simulated minute: update outages, drain batteries, run `silent.ts`, run `ladder.ts`, update statuses.
- At `scenario.outageStart`: every resident in `affectedNeighborhoodIds` gets `powerOut = true`, `outageStartedAt`, and `restoreEstimate` = `scenario.outageEndEstimate`.
- At each entry in `silentBatteries`: stop updating that resident's `telemetryLastSeen` from that time.
- When the plan is ticked for "Charge battery", that resident's `socPct` becomes 100 before the outage starts.

### Demo control bar

Toggled with `Shift + D`. Contains:

| Control | Effect |
|---|---|
| Scenario picker | Load `heat_wave`, `wildfire_unplanned`, `twist_2`, `twist_3` |
| "2 days before" | Set `simTime` to `scenario.startTime` |
| "Night before" | Set `simTime` to `outageStart − 18 h` |
| "Outage starts" | Set `simTime` to `outageStart` and run the minute loop up to that point |
| "+30 min", "+2 hours", "+4 hours" | Advance `simTime`, running every minute in between so ladder steps fire in order |
| Play / Pause | Toggle continuous play |
| Reset | Reload the current scenario |
| Live data on/off | Toggle `liveData` |

---

## 10. Server function: personal plan (`/api/plan`)

A Vercel serverless function so the LLM key is never in the browser.

Request:

```json
{
  "resident": { "name": "Rosa Martinez", "age": 78, "devices": ["oxygen_concentrator"], "batteryWh": 1000, "isRenter": true, "livesAlone": true, "hasEV": false, "language": "es" },
  "forecast": { "probability": 0.74, "windowStart": "...", "windowEnd": "...", "durationLowHours": 12, "durationHighHours": 24, "primaryCause": "wind", "reasons": ["Wind gusts up to 48 mph", "62% tree cover near power lines"] },
  "runwayHoursAtFullCharge": 3.3,
  "nearestCoolPlace": "Torrance Public Library, 1.2 miles"
}
```

### 10.1 System prompt

```
You write short, practical power-outage preparation plans for one specific household.
Use only the facts provided. Do not give medical treatment advice, dosing or diagnosis.
Focus on preparation actions: charging, backups, food and medicine storage, staying cool or warm,
communication with family, and where to go.
If the backup battery covers fewer hours than the expected outage, say clearly that they need a second
power source or a place to go, and include that as an item.
Write in plain language at about a 6th-grade reading level.
Return only JSON matching the schema. 4 to 6 items.
Write the "title" and "reason" fields in the resident's language code given in the input,
and also return an English copy in "title_en" and "reason_en".
```

### 10.2 Response schema

```json
{
  "items": [
    {
      "title": "string",
      "title_en": "string",
      "when": "two_days_before | night_before | one_hour_before | during_outage",
      "reason": "string",
      "reason_en": "string"
    }
  ]
}
```

Validate the response. If it is invalid, return the fallback plan instead.

### 10.3 Fallback templates (`plan_templates.json`)

Provide at least these, keyed by device type plus a `general` list:

- `oxygen_concentrator`: "Charge your oxygen battery to 100% tonight"; "Ask your supplier about backup oxygen tanks"; "If your battery covers fewer hours than the outage, plan where to go"
- `cpap`: "Charge your CPAP battery tonight"
- `home_dialysis`: "Call your dialysis center to confirm a backup plan"
- `insulin_fridge`: "Keep the fridge closed; food and medicine stay cold for about 4 hours"; "Prepare a cooler with ice packs"
- `power_wheelchair`: "Charge your wheelchair fully tonight"
- `general`: "Charge your phone and a power bank"; "Tell {contact} about the forecast"; "Know your nearest cool place: {coolPlace}"; "Keep a flashlight within reach"; "Never use a generator or grill indoors"

---

## 11. Server function: automated call (`/api/call`), stretch goal

- Input: `{ residentId, toPhone, message }`.
- Uses the Retell AI API to place an outbound call with an agent that reads the message and asks the person to press 1 or say "I'm OK". Check Retell's current API docs for the exact endpoint and fields.
- Only used for the live demo moment when the team chooses (e.g. calling a judge's phone after they consent). Behind a separate toggle `liveCall`.
- If it fails, log the message in the feed as if sent and show "Call simulated".

---

## 12. Screens and required behavior

### 12.1 Resident app (`/resident/:id`)

**Setup** (shown if the resident has no saved profile; for sample residents, prefilled and skippable):

- Fields: name, address (or map pin), devices (multi-select from `DeviceType`), backup battery size (Wh or "I don't know"), EV yes/no, renter yes/no, lives alone yes/no, language, contacts (name, relation, phone).
- "I don't know" for battery size uses 500 Wh and marks runway as estimated.

**Today screen** (power on):

- The forecast sentence: "{probability}% chance your power goes out {day} {part of day}" only after the real-data calibration and evaluation gate in section 6 passes. Otherwise show a qualitative risk level with an "Unvalidated estimate" label.
- Expected duration: "{low} to {high} hours" only when supported by evaluated historical outage durations. Otherwise show an assumed planning range labeled "Assumption".
- Primary cause in plain words.
- "Why we think so": expandable list of the 4 reasons.
- Runway at full charge vs. expected outage, e.g. "Your oxygen battery lasts about 3 hours. The outage may last 12 to 24 hours." If runway < durationLow, show a clear warning that they need a second power source or a place to go.
- "Your plan": list of plan items grouped by `when`, each with a checkbox.
- Nearest cool place with name and distance.
- "Sample data" label if the resident is sample data.
- A short source and as-of label for the weather, outage history, and other real data behind the forecast; distinguish live forecasts from archived historical demo weather.

**Outage screen** (power out):

- "Power is out since {time}."
- Battery runway as hours and minutes, updating with the simulation clock. If unknown: "Battery not reporting" plus the last estimate.
- A visual indicator of remaining hours (any form) that changes at the `low` and `critical` thresholds.
- Estimated restore time.
- Large "I'm OK" button. Pressing it pauses the ladder (section 8.4) and logs the event.
- Nearest cool place.
- Status of help, e.g. "A fresh battery is on its way, about 45 minutes."

**Power restored**: "Your power is back." plus a summary of what happened and a prompt to recharge the battery.

### 12.2 Messages feed

- A chronological list of all `MessageEvent`s for the selected resident, newest at the bottom.
- Each item shows sim time, channel (text or call), recipient (Rosa, Elena, neighbor, county) and the text.
- Updates live as the ladder fires.

### 12.3 County view (`/county`)

**Map:**

- All residents as points at their lat/lon.
- Point state by status: `ok`, `low`, `critical`, `unreachable`, `helped`. Each state must be distinguishable.
- Optional layers: fire zones, affected neighborhoods.
- Clicking a point opens the resident detail panel.

**"Runs out first" list:**

- Residents with `powerOut = true`, sorted by `hoursLeft` ascending (unknown/silent first).
- Each row: name, device, hours left (or "not reporting"), status, current ladder step.

**Resident detail panel:**

- Profile summary, forecast, runway, silent reason if any, ladder history for this resident, messages.
- Approve buttons for pending `delivery_requested` and `wellness_requested` steps.

**Help in progress (action log):**

- All `ActionLogEntry`s, newest first, with time, resident, step and description.
- Entries that need approval show an Approve button. Approving sets `approvedBy = "County operator"`, `approvedAt = simTime`.

**Summary numbers:**

- People watched (count of residents).
- Power out now.
- Low / critical / unreachable counts.
- Helped (delivery completed or confirmed OK).
- "ER visits likely avoided" = number of residents who reached `critical` and then reached `helped` before `socPct` hit 0, multiplied by `config.erAvoidanceRate`.
- "Estimated savings" = ER visits likely avoided × `config.costPerErVisit`.
- Both config values live in `src/config.ts`, are editable in the demo bar, and are shown with the label "Assumption". The team must replace the default values with figures from a cited source before the demo.

---

## 13. Scenario files to create

### 13.1 `heat_wave.json`

- Start: a Tuesday 2:00 PM (2 days before). Outage start: Thursday 3:00 PM. Restore estimate: Thursday 9:00 PM.
- Weather: use a documented real historical heat period; record its actual measurements and timestamps in the data manifest. Choose demo clock times to match the source period.
- Alerts: use a real alert from that period if available; otherwise omit the alert.
- Affected neighborhoods: 3 of the inland neighborhoods, including Rosa's.
- Silent batteries: 6 residents in one cell zone go silent at Thursday 4:00 PM with cause `network` (so the network rule shows); 1 resident goes silent with cause `device`.

### 13.2 `wildfire_unplanned.json`

- Start: Monday 9:00 AM. Outage start: Wednesday 6:00 AM. Restore estimate: Friday 6:00 PM.
- Weather: use a documented real historical Santa Ana wind period; record its actual measurements and timestamps in the data manifest. Choose demo clock times to match the source period.
- Alerts: use a real alert from that period if available; otherwise omit the alert.
- The scripted outage represents unplanned fire or wind damage to equipment, not a PSPS event.
- Affected neighborhoods: the hillside neighborhoods in Tier 2/3 fire zones.

### 13.3 Twist files

Create `twist_2.json` and `twist_3.json` when the twists are announced, copying a base scenario and changing only what the twist requires. Put a one-line description of the change in `extraRules`.

---

## 14. Project structure

```
runway/
  public/data/
    residents.json
    neighborhoods.json
    device_watts.json
    plan_templates.json
    fire_zones.geojson
    prediction_data_manifest.json
    scenarios/heat_wave.json
    scenarios/wildfire_unplanned.json
  api/
    plan.ts         # serverless, LLM
    call.ts         # serverless, Retell (stretch)
    sms.ts          # serverless, Twilio (stretch)
  scripts/
    make_residents.py
  src/
    types.ts
    config.ts
    store.ts        # global state: simTime, residents, liveState, messages, actionLog, scenario
    engine/
      risk.ts
      runway.ts
      silent.ts
      ladder.ts
      plan.ts
      simulate.ts   # advances simTime minute by minute and calls the engine
      random.ts     # seeded random generator
    data/
      load.ts       # loads JSON files and live APIs with fallbacks
      weather.ts    # Open-Meteo + NWS clients
    routes/
      Resident.tsx
      County.tsx
      Demo.tsx
    components/     # any UI components
  tests/
    risk.test.ts
    runway.test.ts
    silent.test.ts
    ladder.test.ts
  README.md
```

Suggested tools: React + TypeScript (Vite), a small state store (e.g. Zustand), a map library (Leaflet or MapLibre with OpenStreetMap-based tiles), Vitest for tests, Vercel for hosting and serverless functions. Styling is up to the developer.

---

## 15. Acceptance tests

Unit tests (Vitest):

1. `risk`: with gust 48 mph, tree cover 62%, overhead 0.8, tier3, 4 past outages, no notice, "Wind gusts up to 48 mph" is among the top 4 reasons and the output is within valid bounds. Test fixtures may be synthetic; they are not training or evaluation data.
2. `risk`: adding a PSPS notice elsewhere in the product does not change the unplanned-outage forecast.
3. `risk`: with calm weather (gust 10 mph, temp 75°F), output is within valid bounds and lower than the high-wind fixture.
4. `risk`: a missing or unverified model input never silently receives a fabricated value; the UI uses the unvalidated-risk presentation when the real-data gate is unmet.
5. `runway`: 1000 Wh at 100% with a 300 W device gives 3.33 hours (±0.01).
6. `runway`: after 60 simulated minutes at 300 W on 1000 Wh, socPct drops by 30 points.
7. `silent`: if 4 of 6 residents in a cell zone are silent, each gets `network_likely`; if 1 of 6, it gets `device_likely`.
8. `ladder`: for Rosa in the heat-wave scenario, steps fire in this order: watching, texted_resident, called_resident, notified_family, (notified_neighbor or family confirmed), delivery_requested.
9. `ladder`: `delivery_approved` never happens without an Approve action.
10. `ladder`: pressing "I'm OK" pauses escalation for 2 simulated hours.
11. `plan`: when `/api/plan` fails, a fallback plan with at least 4 items is returned.

Data gate (must pass before a numerical forecast is shown): verify provenance for every model input, use only real historical outage events for fitting and evaluation, prevent event leakage across splits, and publish calibration and performance results on held-out real events.

End-to-end demo check (manual, must pass before submission):

1. Load `/demo`, scenario `heat_wave`, press "2 days before": Rosa's forecast shows a percent and duration only if the data gate passed; otherwise it shows qualitative risk and an assumed duration. Show the window, 4 reasons, data source labels, and a plan with 4 to 6 items.
2. Press "Outage starts": Rosa's screen switches to the outage screen with about 3h 20m left.
3. Press "+2 hours" twice in steps: the messages feed shows the text, the unanswered call, the family text; the county list shows Rosa near the top with status `critical`.
4. Click Approve on Rosa's delivery: the feed shows the delivery messages; after "+30 min" twice, Rosa's battery resets and her status becomes `helped`.
5. The summary shows "ER visits likely avoided" with the "Assumption" label.
6. Reset works and the same sequence plays identically.
7. With `liveData = true` and the network off, everything still works from fallbacks.

---

## 16. README requirements

The repo README must state:

- What Runway does in two sentences.
- How to run it locally (`npm install`, `npm run dev`).
- Which data is real (APIs and public datasets used) and which is synthetic ("All residents, batteries and contacts are sample data").
- The provenance manifest for the real model inputs, the historical outage labels used, the evaluation period and results, and whether the numerical probability and duration gate passed. State plainly when it did not.
- Which tools and AI tools were used, including how IBM Bob was used if entering that prize.
- The responsible-use note: "Runway could expose who depends on medical devices or be used to deprioritize some households. It never cuts power, requires a person to approve deliveries and wellness checks, shows medical details only in the county view, and logs every human decision."
