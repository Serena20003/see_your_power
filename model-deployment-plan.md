# Deployment-ready U.S. outage model

## Summary

Create a separate `model/` directory for the Python data pipeline, training code, forecast service, tests, and container configuration. Connect it to the React app through a versioned API. The public target is **a new unplanned outage anywhere in a county**, with three rolling windows: next 24, 24–48, and 48–72 hours. These are county estimates, not the chance that a particular home loses power.

The first release must also estimate a typical new outage’s duration, conditional on an outage occurring. Planned outages and PSPS appear only in a separate, source-backed frontend layer; neither enters unplanned training labels or predictions.

## Data and model

- Audit public utility and ODIN feeds for explicit outage type, county, start and restoration times, and reporting coverage. Deduplicate events; exclude planned, PSPS, and unknown types. Mark a county-period as a *negative* training example only when reporting coverage is known to be complete.
- Use weather forecasts as they existed at issue time, plus nationwide physical exposure features. Start with NOAA GFS, NWS alerts, canopy, and terrain; test other scored sources through feature ablations rather than treating their review ratings as model weights. NOAA publishes GFS runs every six hours, so hourly issuance can reuse the latest run. [NOAA GFS](https://registry.opendata.aws/noaa-gfs-bdp-pds/).
- Train a seasonal county baseline and logistic baseline, then a pooled gradient-boosted onset model. Calibrate each forecast window on later data. Train a separate conditional duration model using restoration records, accounting for events still open when data is collected.
- Test transfer to counties without local labels by holding out entire utilities and states, including separate checks for Alaska and Hawaii. An eligible county without local history receives a **transferred estimate** badge. EAGLE-I counts may audit coverage but cannot supply unplanned-only labels; ODIN participation and available detail must be checked before use. [EAGLE-I dataset](https://impact.ornl.gov/en/datasets/eagle-i-power-outage-data-2014-2022/), [ODIN guide](https://odin.ornl.gov/api-docs/developer-guide/introduction).

## Service and React connection

- Issue forecasts hourly in a scheduled batch. Publish each complete batch atomically, with model version, issue time, source timestamps, and county coverage status. Serve it through `GET /v1/forecasts/{county_fips}`: three windows, calibrated probability, conditional duration range, reasons, and `local_validated`, `transfer_estimate`, or `unavailable` status.
- Serve source-backed notices through `GET /v1/planned-outages/{county_fips}`. Include source and last-updated time, and show “no usable feed” distinctly from “no planned outages reported.” This endpoint stays outside the model pipeline.
- Replace the preview’s sample numbers with API data, add an accessible county selector and real county boundaries, and relabel the tabs **Next 24h**, **24–48h**, and **48–72h**. Show the transfer badge, forecast freshness, county-level meaning, loading and stale states. Keep household preferences in the separate planning layer; remove sample duration and probability claims from live mode.

## Deployment and release checks

- Use Dockerized Python jobs and a FastAPI read service on Google Cloud Run; Cloud Scheduler runs hourly ingestion and inference. Store versioned raw data and model artifacts in Cloud Storage, and current forecasts, notices, and coverage metadata in PostgreSQL. Use separate staging and production environments, automated builds, secret management, monitoring, and rollback to the previous complete forecast batch. Cloud Run supports scheduled jobs through Cloud Scheduler. [Google Cloud documentation](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).
- Test planned/PSPS exclusion, missing-feed periods, event deduplication, forecast-time leakage, restoration censoring, daylight-saving boundaries, all county FIPS codes, API contracts, React states, and hourly batch recovery. Evaluate probability calibration, Brier score, missed events and false prompts, plus duration interval coverage, on later periods and held-out regions.
- Gate public numeric release on verified typed training data, usable restoration labels, complete nationwide forecast features, and demonstrated regional transfer performance. Select the “plan ahead” threshold for high recall and report its false-prompt burden. If those checks fail, keep the service in internal evaluation and do not present approximate mixed-outage numbers as unplanned probabilities.

## Assumptions

The project is noncommercial and uses public data only. Every county is intended to receive a numeric forecast at launch, including transferred estimates where local labels are unavailable; that outcome is **conditional on the release checks passing**. The current repository contains a static React preview and no model service or training dataset. Planned-outage notices will appear wherever usable public feeds exist. Open-Meteo historical runs can be evaluated for backtesting after confirming API terms and limits; its free API terms specify noncommercial use. [Open-Meteo terms](https://open-meteo.com/en/terms).
