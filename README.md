# Runway frontend mockup

The United States model research page is available at `/model-sources`. The proposed pilot covers all 50 states and DC, with a county-level, 24-hour target for **new unplanned outages only**. It compares candidate data sources, proposed weight shares, a testable formula, label-coverage requirements, and validation steps. The 16 added source scores are priority ratings out of 10, separate from provisional forecast feature shares. California-only feeds are marked as regional examples; response-planning and live-confirmation data do not automatically become forecast inputs. PSPS and maintenance shutoffs are excluded from predictor inputs and training labels. Shares are hypotheses, not fitted or calibrated model coefficients.

A static React preview of a weather-style power forecast that turns into a household plan. It has no API calls, accounts, telemetry, or live emergency features. The map, forecast numbers, rate periods, resident details, and assistant replies are illustrative sample data.

The interface uses IBM's Carbon Design System components, Carbon icons, the light `g10` theme, and locally bundled IBM Plex Sans fonts. It adapts from desktop to phone and includes a web app manifest for a home-screen presentation. Offline caching and live alerts are future work.

## Run locally

```sh
npm install
npm run dev
```

Open the local address Vite prints. `npm run build` creates a static site in `dist/`.

The county risk map uses the [Carbon Gray 10 Mapbox style](https://www.carbondesignsystem.com/building-blocks/data-visualization/spatial-charts/#mapbox). For local development, set `VITE_MAPBOX_ACCESS_TOKEN` to a Mapbox public access token in an ignored `.env.development.local` file. Set the same variable in the deployment environment when building the site. County risk dots are GeoJSON layers on the map, so they stay aligned while panning and zooming.

## Current interaction

The first-use questions ask about care needs, flexible routines, costs, and family check-ins. You can answer them or continue with the sample profile. Select a forecast day and toggle between power, wind, heat, and sample energy-cost layers. The schematic map stays centered on the example resident's area.

The plan and profile settings share one panel. Every action includes a visible reason. Settings show each answer, its source (sample, starting questions, chat, or manual correction), and a control to change it. The local scripted assistant recognizes a few topics (EV, insulin, laundry, family, and bills) and updates the example profile. Changes affect the plan immediately, but this preview does not call an AI service or remember anything after the page closes.

## Where the model connects later

The local `days` objects in `src/mockModel.ts` stand in for future forecast results. The model should provide an area identifier, issue time, outage probability, window, expected duration range, reason labels, validation status, and data provenance. A separate tariff source would provide actual prices. `createPlan` combines the forecast with the resident's devices, routines, and cost preference. The schematic map in `src/MapScene.tsx` would later use real geographic layers with a clear as-of time.

The prediction model itself is not part of this mockup. Numerical probabilities must be trained, calibrated, and evaluated on real outage records before they are presented as genuine forecasts. The browser could later cache the app and last forecast for offline reading, with an as-of time and a clear stale-data state. Any assistant memory should be opt-in, reviewable, and removable by the resident.
