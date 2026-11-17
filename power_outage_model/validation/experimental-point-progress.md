# Experimental random-point execution ledger

Plan: `power_outage_model/EXPERIMENTAL_POINT_PLAN.md`.

November 17, 2026: execution started in the requested subagent. Existing model directory is approximately 8.3 GB on disk; no user data will be removed. Work is isolated to `outage_model.experimental`, its tests/docs and validation artifacts. Strict observed-incident training/release gates remain unchanged.

- Task 1: source access and compact streaming feasibility in progress.

- Task1 complete: found the original authors' public Figshare source, avoiding Globus authentication; verified all999,210,542 transferred bytes and published MD5. Streamed21,776,806 records into1.73MB compact county-day aggregates; excluded2,027,828 blanks. Raw CSV never stored.
- Task2 complete: Census .prj4269->4326 verified; equal-area generalized-polygon sampling, explicit retrospective/inland-water support caveat; nonoverlapping3-day anchors cover all weekdays, parent splits before simulation, mass conserved with8 proxies.
- Task3 complete: real fit finished in42.6s. Entire dataset180k parent groups/1.44M points/2,758 counties; actual fit121,940 parents/975,520 points/2,474 counties. Later-time MAE222.72 vs baseline227.21; severe-total underprediction retained and disclosed, not retuned after test. Report labels group-disjoint parents rather than statistically independent events.
- Task4 complete: explicit experimental inference, fixed UTC-day windows, source-support/range/year-extrapolation flags, full2,758-county output (8,274 windows/66,192 points), standalone~0.88MB inference ZIP with assumption and CC BY4 source manifests. Fresh-store API parity/CLI validation passes without matrices. Final focused26 checks pass (12 experimental+14 unchanged model/raster checks), compileall/pip check pass. Strict probability publisher/frontend/cloud unchanged. No user files removed; final directory approximately8.30GB under10GB hard ceiling.

Ruling: EAGLE-I omissions conflate zero and gaps, so fit a conditional allocated-impact regression instead of inventing negative incident labels. Cost: this model cannot estimate occurrence probability.
Ruling: use calendar/geographic features only, because no matching historical forecast/canopy input was obtained. Cost: current DWD weather does not influence these experimental predictions.
Ruling: keep generalized2025 county polygons with explicit CRS conversion and retrospective/inland-water caveat. Cost: simulated support is approximate and does not establish2018 service land/asset locations.
