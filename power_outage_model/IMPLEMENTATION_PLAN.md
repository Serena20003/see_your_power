# Explainable boosted-tree outage probabilities: implementation plan

**Date:** November 16, 2026 (America/Los_Angeles).  
**Status:** Implementation plan only; no training, collection job, deployment, or frontend change has been performed.  
**Goal:** Produce auditable probabilities of at least one new unplanned outage per fixed grid cell for three consecutive 24-hour windows, using forecast weather and canopy, with California as the first label adapter and a common multi-source label contract.  
**Architecture:** Three pooled LightGBM binary classifiers, one per horizon, followed by independently fitted probability calibration. Compare global calibration with regularized regional calibration; retain regional adjustments only if held-out evaluation supports them. Consume immutable outputs of the weather pipeline through a versioned adapter, and publish separately from its environmental heuristic.  
**Execution:** Implement the numbered tasks sequentially, checking their acceptance tests before moving on. The user has confirmed that the hourly DWD weather pipeline implementation is complete. Integrate against its current versioned raster contracts and pin a representative complete batch as an integration fixture. This document does not require changes to that pipeline.  
**Specification:** The user decisions and technical design below are the specification for this plan. The existing hourly raster plan is an upstream dependency, not the supervised model's target definition.

## 1. Decisions and scope

User-confirmed decisions:

- Predict the probability of at least one new unplanned outage per grid cell in each 24-hour window: next 24 hours, 24–48 hours, and 48–72 hours.
- Support a common format for multiple utility/state feeds; implement California first.
- Show a clearly labeled weather risk score where outage probabilities have insufficient validation.
- Use boosted trees, with transparent variable definitions, model selection, and explanations.
- Place this work inside `power_outage_model/` and consume the completed 72-hour DWD raster pipeline as its upstream producer.

Engineering decisions in this plan, open to revision based on measured results:

- Use existing precipitation, temperature, wind speed, and canopy inputs initially. Derive explicit temporal and neighborhood summaries; add no new meteorological source in the first iteration.
- Start with pooled trees and compare global versus partially pooled regional calibration. Do not describe a pooled tree ensemble alone as a hierarchical model.
- First geographic domain is **CONUS**, matching the upstream grid. Alaska, Hawaii, territories, and a claim of full U.S. coverage require later weather, canopy, label, and validation extensions.
- Keep California transmission exposure out of predictors: transmission coverage is geographically uneven and does not establish distribution-grid exposure.
- Predict new outage incidents, not customers interrupted, ongoing-outage prevalence, equipment failures, restoration time, or household outage probability.
- Build offline training, evaluation, inference artifacts, and a documented map-consumption contract. Frontend implementation and changes to `model/` are separate work.

The classifier learns an association with a source-defined outage incident location. Where sources provide approximate markers rather than failure locations, document that limitation in the target and model card. Do not call this an asset-failure model. Sources whose spatial semantics cannot support the fixed-cell target are excluded from that target rather than silently assigned precise labels.

## 2. Existing implementation and dependency boundary

Read these files before implementing:

- `raster_pipeline/contracts.py`: `GridSpec`, `WeatherCube`, `StaticContext`, `NODATA`, UTC utilities.
- `raster_pipeline/run.py`: `raster-batch/v1` manifests, 72 ordered weather objects, supporting intervals, input/static provenance.
- `raster_pipeline/publish.py`: currently permits only uncalibrated environmental scores and rejects probabilities.
- `raster_pipeline/storage.py`: immutable object writes and conditional pointer replacement.
- `raster_pipeline/retention.py`: weather COG retention is currently seven days; raw inputs/manifests generally thirty days.
- `README.md` and `../docs/superpowers/plans/2026-11-16-hourly-weather-risk-rasters.md`.
- `../docs/research/odin-point-data-access-2026-11-16.md` and `../docs/research/odin-source-report-2026-11-16.md`.

Upstream currently supplies a 472×208 EPSG:4326 grid at 0.125 degrees over [-125,24,-66,50], plus a CONUS land mask. Geographic cells do not have constant physical area. Weather bands are hourly interval-total precipitation in mm, endpoint temperature in °C, and endpoint wind speed in m/s. Wind speed is not gust speed; endpoint samples are not hourly means.

**Do not replace `RiskCalculator` with the supervised classifier.** Its hourly interface and score-only publisher do not express three 24-hour probabilities. Add a downstream `outage_model/` package and a separate `outage/v1/` storage namespace. Read weather artifacts; never train on the heuristic risk score.

**Archival dependency:** copy the complete, verified weather dependency set into `outage/v1/archive/` before upstream retention can delete it. Copy weather COGs, required static objects, manifests, and raw inputs needed to reproduce normalization; preserve original keys/checksums in a remapping manifest. Upstream GC only scans `raster/v1/`. Do not assume a reference from the new model package protects an upstream artifact. Incomplete archive copies cannot become eligible training snapshots. Storage lifecycle policies must also preserve the archive.

The upstream software implementation is complete, as confirmed by the user. Begin model integration with the completed batch manifest, archive its dependency set, and test feature extraction against its 72 actual hourly intervals. Model contracts, source adapters, event normalization, coverage auditing, split logic, and explanation interfaces can proceed alongside this integration. Real fitting still needs aligned forecast history and reliable outage labels; public probability release additionally needs untouched evaluation evidence. Completion of the weather software does not itself establish the length of the historical forecast archive or outage-label coverage.

## 3. Prediction and label contract

### Time and identity

- Freeze `cutoff` to the upstream batch cutoff and `target_start` to its first target interval. Never substitute model initialization or wall-clock completion for cutoff.
- Windows are `[target_start,target_start+24h)`, `[+24h,+48h)`, `[+48h,+72h)` in UTC. Boundaries are half-open; an onset at +24h belongs to the second window.
- Features may use all forecast values inside their target window if those forecasts were available by cutoff. Future observations, updated forecasts arriving after cutoff, and later-issued alerts are excluded.
- Row key: `(archive_id, grid_id, row, col, horizon_id)`. `horizon_id` is `0_24`, `24_48`, or `48_72`.
- Labels mature only after window end plus a source-specific audited reporting lag. Dataset freeze time, lag policy, and late revisions are recorded.

### Canonical records

Define frozen Pydantic records in `outage_model/contracts.py`:

| Record | Required content |
| --- | --- |
| `RawSnapshot` | Source, URI/query, retrieval time, source update time if present, paging/completeness evidence, raw checksum, adapter/schema version, explicit fixture flag |
| `OutageEvent` | Canonical ID, source/utility IDs, original event ID, onset and its quality, first/last seen, normalized outage type, type-mapping evidence, geometry/CRS, geometry meaning/quality, county if supplied, source snapshot references |
| `CoverageInterval` | Source/utility reporting domain, exact UTC bounds, verified geographic footprint version, expected/received polls, completeness status and evidence, exclusions, type/location/onset limitations |
| `LabelRow` | Row key, window bounds, nullable binary label, deduplicated new-event count, coverage evidence IDs, event IDs, label exclusion reason, label maturity/freeze time, evaluation group IDs |
| `FeatureTable` | Ordered feature names, numeric matrix, row keys, provenance and eligibility masks; metadata columns separate from predictors |
| `PredictionBatch` | Upstream archive reference, model/calibration IDs, three windows, probability and support objects, fallback references, explanation references, checksums, publication status |

Rules:

1. Include explicitly classified unplanned events only. Exclude planned, PSPS, and unknown classifications. `Not Planned` mapping needs audited source semantics; a POINT representation or missing type is not proof of unplanned status.
2. Preserve source/utility/event identity across snapshots. Repeated active incidents yield one onset, not a new incident every poll. Ambiguous ID reuse or cross-feed matches go to quarantine; prefer duplicate Cal OES/direct-utility records through a versioned source-priority rule.
3. Estimated restoration time and snapshot retrieval time do not establish actual onset. First-seen can bound an onset only after a verified prior complete snapshot; quarantine intervals crossing target-window boundaries.
4. A successful HTTP request or empty response does not establish complete reporting. Require paging completion, schema validity, acceptable freshness, stable footprint, and source-specific omission audits. Polling current maps may miss incidents that start and finish between polls; audit this explicitly. Without sufficient ascertainment, the target is reported incidents, and physical-outage probability claims are disallowed.
5. Binary training rows require complete reporting for the **whole supported land portion of the cell and the entire window**, across relevant utilities. Partial utility footprints do not establish a whole-cell negative. Exclude both positive and negative rows from incomplete periods to avoid outcome-dependent sample selection; retain events separately for audit.
6. Count new incidents, not the county layer's customers without power. An incident spanning several cells is not allocated to all cells without source-supported geometry semantics. Initial adapter accepts qualified incident markers; polygon-only sources need a separately reviewed label policy.
7. Deterministically assign qualified points on interior grid edges to the eastern/southern cell; west/north outer edges are included and east/south outer edges excluded. Test orientation against the upstream north-up transform. Record location-uncertainty sensitivity near cell boundaries rather than inventing exact coordinates.
7a. Early-incident coordinate refinements from live sources (e.g. Cal OES updating a marker by tens of metres in the first few polls) are sub-grid noise at 0.125° resolution (~14 km × 11 km cells) and do not require quarantine. `LabelPolicy` accepts an optional `location_revision_tolerance_m` field (default `1000`, matching the recommended Cal OES threshold; set to `0` to restore strict equality). When set above zero, `reconcile_events()` computes the geodesic shift; shifts strictly below the threshold update the stored location silently and always adopt the latest-seen coordinates; shifts at or above the threshold, or shifts where either coordinate is missing, still trigger `location_revision_requires_audit` quarantine. A threshold of 1000 m is recommended for Cal OES (well under one grid cell, but large enough to flag genuine county-level relocations). The raw per-poll coordinates are preserved in the immutable snapshot archive and are always recoverable for audit.
8. No imputed negative labels, synthetic training history, outage-label spatial interpolation, or pseudo-labeling unobserved states.

### California first

Implement a Cal OES ArcGIS incident-point adapter with schema discovery/pinning, object-ID enumeration or verified pagination, WGS84 conversion, raw snapshot retention, source-specific type mappings, and ID/onset auditing. Default prospective collection cadence is 15 minutes, matching the documented refresh; this is not proof that brief outages are captured. First snapshot initializes an active-event inventory and does not turn existing incidents into new onsets. Feed failure, stale records, missing pages, or parser errors create coverage gaps.

The public California dataset contains recent outages rather than historical records. Starting this adapter builds a prospective archive; it does not unlock a historical training set. A history-access inventory must separately list each source's earliest usable date, gaps, access/redistribution conditions, onset quality, spatial meaning, and ascertainment evidence. ODIN history remains an optional future adapter requiring verified access and schema; no access is presumed.

## 4. Feature registry: every predictor is explicit

Create `outage_model/configs/features-v1.json` and generate `outage_model/docs/FEATURES.md` from it. Each entry includes name, family, exact recipe, unit, physical motivation, source/availability, valid range, missing behavior, geographic coverage, vintage, limitations, and monotonicity decision. Unknown names or feature order changes invalidate an artifact rather than silently changing inference.

Initial candidates, computed separately for each 24-hour window:

| Names | Exact definition and intended information |
| --- | --- |
| `wind_max_ms`, `wind_mean_ms` | Max and arithmetic mean of the 24 endpoint wind-speed samples; storm intensity and persistence, not gusts or continuous hourly means |
| `rain_total_mm`, `rain_max_1h_mm` | Sum and max of 24 hourly precipitation totals; accumulation and hourly intensity |
| `temp_min_c`, `temp_max_c`, `temp_mean_c`, `temp_range_c` | Min, max, mean of endpoint samples, and max minus min; cold/heat exposure and variability |
| `canopy_fraction` | Versioned area-weighted canopy fraction on valid land; environmental context, not trees adjacent to conductors |
| `neighbor_wind_max_ms`, `neighbor_wind_mean_ms` | Max and land-area-weighted mean of `wind_max_ms` over a centered 3×3-cell neighborhood |
| `neighbor_rain_max_mm`, `neighbor_rain_mean_mm` | Max and land-area-weighted mean of `rain_total_mm` over the same neighborhood |
| `neighbor_canopy_mean` | Land-area-weighted canopy mean over that neighborhood |
| `season_sin`, `season_cos` | Sine/cosine of 2π times the UTC window midpoint's fractional position in its calendar year; seasonal context |
| `analysis_land_area_km2` | Geodesic or equal-area measure of analysis land in the cell; adjusts for unequal spatial support, not asset exposure |

Neighborhoods include the center. Exclude ocean and cells outside the CONUS analysis mask; normalize means by valid land area, not nominal cell counts. A missing required weather/canopy value in an in-domain contributing cell makes neighborhood features ineligible; do not treat missing as calm/dry/zero canopy. Grid-edge truncation is deterministic and documented. A 3×3 neighborhood has varying physical dimensions by latitude; report these and test a metric-radius alternative only as a separate feature version.

Exclude from v1 predictors: latitude/longitude, cell/county/state/utility/source IDs, collection uptime, reporting counts, prior outage labels, heuristic scores, California-only transmission inventory, customers interrupted, and any post-cutoff information. Geographic IDs remain available for grouping, calibration routing, and evaluation; that use is also disclosed. No fabricated terrain, soil moisture, gusts, icing, or distribution assets.

Start without monotonic constraints: raw temperature is not monotonic in risk, and even plausible wind/rain directions require checking interactions. Document every constraint as `none` initially. A constrained challenger needs a physical justification and the same transfer/calibration checks. Trees learn interactions, so do not add arbitrary fixed weather weights. Feature admission/removal is decided from grouped ablations on development data, not from test-set SHAP rankings.

## 5. Model choice, training, and regional structure

### Models to compare

1. Smoothed seasonal-frequency baseline fitted on training labels, with pooled fallback for sparse groups. Use calendar quarters and a Beta(1,1) binomial prior; record every count and fallback.
2. Regularized logistic baseline using the same candidate inputs, with train-only scaling. Select regularization on development folds; disclose coefficients and limitations for nonlinear responses.
3. **Selected candidate:** one LightGBM binary-log-loss classifier per 24-hour horizon. This directly models window occurrence, without assuming independent hours or multiplying hourly probabilities.

LightGBM is chosen for tabular summaries, nonlinear interactions, bounded complexity, CPU inference, inspectable tree exports, and built-in additive feature contributions. This is a testable choice, not a claim that it already beats the baselines. A CNN or graph model is deferred because sparse labels do not yet justify the added complexity; the first spatial representation is explicit neighborhood summaries. If trees do not beat the baselines, report that result and keep probabilities internal pending a documented model decision.

Initial settings: CPU, `objective=binary`, `metric=binary_logloss`, `learning_rate=0.03`, `max_depth=3`, `num_leaves=7`, `min_data_in_leaf=100`, `lambda_l2=1`, no bagging, all features, all seeds=42, `deterministic=true`, `force_col_wise=true`, up to 1000 rounds, early stopping after 50 rounds on tuning data. Bounded challenger grid: depth {3,5}, leaves {7,15} subject to leaf/depth compatibility, minimum leaf {100,500}, L2 {1,10}; keep other settings fixed. Record every attempted run. Pin compatible LightGBM/scikit-learn/Parquet dependencies in a separate model dependency lock at implementation; do not silently upgrade the raster environment.

Compare natural-frequency training with a capped utility/reporting-domain balancing scheme. For an unambiguous complete reporting domain g, use `w_raw=clip(median(n_g)/n_g,0.2,5)` based on training-only row counts; normalize weights to mean one. Multi-utility complete-cell unions get their own disclosed domain key. Do not weight on label value or oversample positives. Choose the scheme on development geographic folds, and calibrate/evaluate using deployment-frequency rows. Publish both macro-domain and row-weighted metrics so neither objective conceals the other. This weighting addresses dominance, not unobserved reporting bias or missing hazard regimes.

### Calibration, including an actual partial-pooling candidate

For horizon h and raw tree margin f, fit a global sigmoid `p=sigmoid(a_h*f+b_h)`, with `a_h>=0`, on a later calibration block. Compare a regional extension `p=sigmoid(a_h*f+b_h+delta_rh)` with L2-regularized regional offsets. Fit offsets jointly with the global terms; use a zero-centered penalty and an identifying zero-sum offset constraint. This is penalized partial pooling (equivalently MAP under a stated Gaussian prior), not a fitted Bayesian posterior.

Use a pinned, documented NOAA-style nine-climate-region membership table as calibration routing metadata, assigned by grid-cell center with a boundary flag. It is not a tree predictor or a substitute for climate variables. Fit an offset only for regions with at least 100 positive and 1000 negative calibration rows from at least 20 independent evaluation blocks; otherwise set its offset to zero and disclose global fallback. Select penalty λ from {1,10,100} on inner chronological folds within the calibration block; final calibration refits only that block. Regional calibration wins only if separate gate-validation results improve macro-region Brier score without worsening pooled Brier score or violating a regional gate; otherwise retain global calibration. Unknown regions use zero offset and receive no automatic validation entitlement.

Keep calibration distinct from learned weather effects. No utility correction is fitted in v1, avoiding ambiguous utility membership in whole-cell predictions. Unseen utilities and states still require transfer evaluation even if their regional offset is known. Do not issue narrow uncertainty intervals from partial pooling alone.

## 6. Transparency deliverables

Every model release includes:

- Dataset inventory and exclusion audit; event/coverage lineage, spatial and temporal coverage, event and negative counts by utility/state/hazard block, known omissions, static vintages, and availability assumptions.
- Feature registry and admitted-feature list; exact formulas/order/units, dependency graph, missing rules, and family ablation results.
- Model comparison with every trial, selection criteria, parameters, seeds, code revision, environment lock, train/tune/calibration/gate/test assignments, and held-out results.
- Native LightGBM text models and JSON tree dumps, numeric calibration parameters/routing, and checksums. No opaque pickled estimator is required for inference.
- Global grouped permutation importance on development holdouts, contribution distributions, and response plots with data-support marks. Correlated features can share/substitute importance; do not interpret gain importance as physical importance or fitted weights.
- A model card describing prediction meaning, CONUS scope, reported-marker limitations, class imbalance, validation limits, failure cases, support policy, and the distinction between association and causation.

For every valid scored cell/window, `explain(row_key)` returns raw feature values and provenance, expected raw margin, **all** signed feature contributions, raw margin, calibration slope/intercept, regional offset/fallback, calibrated probability, validation/support status, model IDs, and checksums. Human summaries may show top factors but must link to the complete record and name the reference baseline. Do not generate causal explanations such as “rain caused this outage.”

Use LightGBM `pred_contrib` with the same selected tree count as prediction. Verify `base + sum(contributions) == raw_margin` within 1e-6. After sigmoid calibration, verify `a*base + sum(a*contributions) + b + delta == calibrated_log_odds`. Report contributions in log-odds units, not additive percentage points. If calibration slope is zero, all scaled weather contributions are zero. Regional adjustment is an explicit separate contribution. Grouped sums must exactly recover the full sum. Explanations reproduce the deployed calculation; they do not prove causality or resolve missing variables.

Persist the scored feature table for each batch. Full contributions can be computed deterministically on demand from the pinned model and row; store representative full explanations plus hashes, rather than requiring a huge per-feature raster stack every hour. Benchmark full-grid explanations separately.

## 7. Evaluation, support, and release rules

### Split manifest

Use five chronological blocks with approximate proportions 50/15/15/10/10: train, tune, calibrate, gate-validation, and untouched final test. Config pins exact UTC boundaries after the data inventory; proportions never override event grouping or minimum support. Train preprocessing/weights on train only, select trees/features on development folds, fit calibration only on calibration, choose the candidate/support policy on gate-validation, and evaluate the frozen decision once on final test. Do not refit on final test and keep its scores as if it were untouched.

Assign each row the complete feature/target time span and a versioned weather-event block ID. A reviewed `event_blocks.parquet` groups storm episodes across state lines; quiet periods use fixed UTC calendar-week IDs. Create this grouping from weather/event evidence, independently of model errors. Purge rows crossing split boundaries and require a 72-hour embargo between different chronological roles; enlarge it if feature history is introduced. Shared onsets, overlapping target windows, and cross-state storm IDs cannot straddle fit/evaluation roles. Tiny synthetic examples test this logic; they are not production evidence.

Within development, repeat leave-one-utility and leave-one-state-out experiments, with **all** preprocessing, calibration, support estimates, and model selection excluding the held-out group. A fold that cannot provide independent storm/time coverage is reported ineligible. Also report leave-entire-storm-out results. Final reporting separates later-time local skill, geographic transfer skill, and joint new-geography/new-event skill. California-only data can support local/utility tests; it cannot establish nationwide transfer.

### Metrics and initial gates

Report Brier score and skill against both baselines, log loss, calibration intercept/slope and reliability bins, PR-AUC with prevalence, recall/precision/false alerts at disclosed thresholds, and macro state/utility/event-block metrics. Include season, wind/rain/temperature severity, canopy, horizon, sparse/dense data, and spatial-boundary slices. Bootstrap **independent event blocks**, not pixels, for uncertainty; do not equate thousands of neighboring rows with thousands of independent events.

Initial public-probability gates below are conservative **engineering proposals**, not scientific guarantees. Version them before model selection. Changing them after seeing final test requires a new future test period.

- Verified label typing, spatial/onset semantics, full-cell ascertainment, archive availability, and mature labels; no critical lineage/leakage errors.
- At least 100 positive and 1000 negative eligible final-test rows, 20 independent event blocks, and 90 distinct evaluated days for each proposed geographic/horizon validation scope. These are minimum screening counts, not sufficient proof of coverage across all seasons; release applicability is restricted to evaluated seasonal/hazard support.
- Positive Brier skill against each baseline, with the 95% event-block bootstrap lower bound above zero for both pooled and equal-domain-weighted comparisons in the declared scope. Use 1000 replicates, seed 42; undefined/degenerate intervals fail closed.
- Ten equal-frequency reliability bins, each with at least 100 observations; mean absolute calibration gap <=0.05 and maximum bin gap <=0.10. Report bin intervals and sample counts; insufficient bins fail rather than being omitted.
- No eligible geographic/hazard slice has negative Brier skill against the seasonal baseline; slices lacking evidence are unsupported. Report the unsupported extent explicitly.
- Successful explanation reconstruction, baseline comparison, input integrity, and no production/fixture mixing.

Validation scope is an explicit polygon/season/horizon artifact, conservatively limited to evaluated reporting domains; never stamp an entire state validated because one utility passed. Every batch also checks input eligibility and support. As an initial out-of-distribution screen, fit robust scaling on training features, retain a fixed-seed reference sample up to 10,000 rows, and set a nearest-reference distance cutoff at the 99th percentile of training leave-one-block-out distances. Check all features against training min/max as well. Save scaling, reference keys, distance metric and threshold; this is a support screen, not a confidence interval. Coverage approval, distance support, and passing min/max checks are all necessary, and none guarantees accuracy.

### Publication semantics

Publish three separate probability COGs, a UInt8 status raster per horizon, and corresponding three 24-hour heuristic score COGs. Probability is Float32 [0,1] only in supported validated cells; elsewhere NoData=-9999. Status codes: 0 outside domain, 1 validated local probability, 2 insufficient validation, 3 outside feature support, 4 invalid inputs, 5 stale forecast; NoData=255. Transferred experimental probabilities remain internal in v1, consistent with the chosen public fallback policy.

Derive each fallback score as the **maximum of the existing 24 hourly environmental heuristic scores** within that window, with complete valid coverage required. Name it “Peak hourly weather risk score in this 24-hour window”; it remains uncalibrated and is not an outage probability. Include the upstream method/config hash and aggregation recipe. Missing/stale weather yields unavailable/stale status rather than a fresh-looking fallback. No palette or numeric field may silently mix scores and probabilities.

The model runs after a complete upstream batch, not inside its ingestion job. Use a separate immutable manifest and `outage/v1/latest.json` pointer, conditional publication, and rollback to a previous complete model batch. Require all three windows and explicit statuses, not probabilities for all cells. Reusing an old batch preserves its timestamps; mark it stale when `now - upstream.cutoff > 2 hours`. Republishing or rescoring old weather never resets this clock. The age budget is an engineering default and must be recorded. Keep source-run age, upstream cutoff age, and model publication age as separate fields. Map consumers must recompute age when reading a batch; an immutable status raster cannot anticipate becoming stale.

County summaries are not probabilities in v1. Do not multiply independent cell probabilities, smooth them into larger-cell probabilities, or interpret mean cell probability as a household/county chance. Probability COG overviews use nearest sampling for display only; coarse tiles retain original-cell semantics.

## 8. Proposed file layout

All paths below are relative to `power_outage_model/`. Create files when the owning task is implemented, not as empty scaffolding now.

```text
outage_model/
  contracts.py                   # Immutable data/model/prediction contracts
  archive.py                     # Durable content-verified upstream snapshots
  adapters/base.py               # Common snapshot/normalization protocol
  adapters/california.py         # First Cal OES incident adapter
  labels.py                      # Event identity, coverage, mature labels
  features.py                    # Shared batch training/inference transforms
  splits.py                      # Chronology, storm/geography holds, embargo
  baselines.py                   # Seasonal and regularized logistic models
  train.py                       # Three pooled classifiers; bounded trials
  calibration.py                 # Global sigmoid and shrunk regional candidate
  evaluate.py                    # Metrics, block bootstrap, promotion evidence
  support.py                     # Scope and input-support eligibility
  explain.py                     # Full additive prediction reconstruction
  artifacts.py                   # Versioned checksum-validated model bundle
  predict.py                     # Three-window inference and fallback selection
  publish.py                     # Separate COG/manifest publication contract
  cli.py                         # Explicit collect/archive/dataset/train/score commands
  configs/{features-v1,train-v1,release-v1,regions-v1}.json
  docs/{FEATURES,MODEL_CARD_TEMPLATE,DATA_SOURCES}.md
tests/outage/                     # Tests named by task below
requirements-model.txt            # Direct model-only dependencies
requirements-model.lock           # Resolved Python 3.12 model environment
Dockerfile.model                  # Model job; preserve raster Dockerfile
```

## 9. Implementation tasks and acceptance tests

Run commands below from `power_outage_model/` in the separately locked model environment. Each task adds the named tests, proves they fail for the missing behavior, implements its interfaces, and reruns its focused tests. Record reviewable changes per task; do not commit unrelated working-tree changes.

### Task 1 — Freeze model contracts and preserve upstream history

**Files:** create `outage_model/contracts.py`, `archive.py`, `configs/features-v1.json`; create `tests/outage/test_contracts.py`, `test_archive.py`.  
**Interfaces:** `archive_batch(store, manifest_key: str) -> ArchiveRef`; `load_archive(store, ref: ArchiveRef) -> ArchivedBatch`. Define `ArchiveRef`, `ArchivedBatch`, `RowKey`, `FeatureTable`, `SplitManifest`, `ModelBundle`, `PredictionBatch`, `ReleaseEvidence`, and the label records in the contracts module; later tasks refine schemas through explicit version changes.

- [ ] Pin the supervised target/window schemas and sample `raster-batch/v1` adapter fixture; fail on incompatible grid, units, temporal semantics, missing checksums, or reordered/duplicate intervals.
- [ ] Archive complete weather/static/raw dependency closure with content hashes and rewritten references; mark reconstruction availability separately from normalization reproducibility.
- [ ] Test deletion of original raster objects after copying still permits feature reproduction; missing dependencies and checksum errors prevent archive promotion; fixture/live mixing is rejected.
- [ ] Run `python -m pytest tests/outage/test_contracts.py tests/outage/test_archive.py -q`; all pass. No changes to upstream score-only publication/retention are needed.

### Task 2 — Common source protocol and California adapter

**Files:** create `adapters/base.py`, `adapters/california.py`, `docs/DATA_SOURCES.md`, `tests/outage/test_california.py`.  
**Interfaces:** adapter protocol `fetch_snapshot(client, observed_at) -> RawSnapshot` and `normalize(snapshot: RawSnapshot) -> list[OutageEvent]`; `audit_snapshot(snapshot, previous, policy) -> list[CoverageInterval]`.

- [ ] Pin ArcGIS layer metadata/field mappings from a real read-only schema check; document object-ID stability, exact type/onset fields, update cadence, omission limitations, and access conditions.
- [ ] Implement read-only paged collection with retry, schema checks, raw evidence, and no automatic complete-coverage declaration.
- [ ] Test missing pages, schema drift, stale responses/events, empty-but-unverified response, timezone conversion, unknown types, prior active records, and no historical backfill claim.
- [ ] Run `python -m pytest tests/outage/test_california.py -q`; all pass. A fixture collection is explicitly marked test-only; production scheduling waits for an inspected source policy.

### Task 3 — Deduplicated events, ascertainment, and labels

**Files:** create `labels.py`, `tests/outage/test_labels.py`; extend source audit documentation.  
**Interfaces:** `reconcile_events(snapshots, policy) -> EventLedger`; `build_labels(ledger, coverage, row_keys, freeze_time, policy) -> list[LabelRow]`. Define `EventLedger` and source/label policy records in `contracts.py`.

- [ ] Implement source-priority deduplication, onset qualification, boundary assignment, reporting-domain unions, label maturity, and exclusion reasons.
- [ ] Test planned/PSPS/unknown exclusion; repeated snapshots; ID reuse; cross-feed duplicates; stale/estimated onset; exact +24h and +48h boundaries; spatial edge ownership; uncertain marker semantics; short-outage ascertainment limits.
- [ ] Test multi-utility partial cells and missing polling periods yield null labels even when one positive is known; complete eligible empty windows produce zero; late revisions version the label snapshot.
- [ ] Run `python -m pytest tests/outage/test_labels.py -q`; all pass; produce a coverage/exclusion audit before fitting anything real.

### Task 4 — Shared, registered spatial/temporal features

**Files:** create `features.py`, `docs/FEATURES.md`, `tests/outage/test_features.py`.  
**Interfaces:** `build_features(batch: ArchivedBatch, registry) -> FeatureTable`; `render_feature_docs(registry) -> str`. Training and inference call this exact function.

- [ ] Implement the 17 candidate features in section 4, in registry order, and per-horizon provenance/eligibility. Preserve endpoint-versus-interval semantics.
- [ ] Test a hand-calculated 3×3 neighborhood, unequal land areas, land/ocean edges, valid zero canopy, NoData propagation, leap-year season phase, and exact three-window partitioning of 72 hours.
- [ ] Test forecast valid time after cutoff is allowed when source availability precedes cutoff; a later-issued forecast or observed future weather is rejected. Test historical static vintages and explicit retrospective-only flags.
- [ ] Run `python -m pytest tests/outage/test_features.py -q`; all pass; generated feature documentation matches the registry exactly.

### Task 5 — Reproducible datasets and independent evaluation splits

**Files:** create `splits.py`, `tests/outage/test_splits.py`; add dataset assembly commands in `cli.py`.  
**Interfaces:** `assemble_dataset(labels, features, freeze_time) -> DatasetSnapshot`; `make_splits(dataset: DatasetSnapshot, policy) -> SplitManifest`. Define immutable snapshot and split policy records in `contracts.py`.

- [ ] Write content-addressed Parquet tables, an exclusion ledger, archive/label/feature hashes, exact dates, reviewed event blocks, and immutable row-key assignments.
- [ ] Implement five chronological roles, 72-hour embargo, overlapping-target purges, entire-storm grouping, state/utility holdouts, and training-only preprocessing/weights.
- [ ] Test the same incident in several hourly-issued windows cannot leak across roles; a storm crossing two states remains one event block; held-out groups cannot enter calibration/support references; insufficient history returns an explicit data-readiness failure.
- [ ] Run `python -m pytest tests/outage/test_splits.py -q`; all pass. Publish a dataset card including effective event counts and missing geographic/hazard regimes.

### Task 6 — Baselines and bounded tree training

**Files:** create `baselines.py`, `train.py`, `configs/train-v1.json`, model dependency files; create `tests/outage/test_train.py`.  
**Interfaces:** `fit_baselines(dataset, splits, config) -> BaselineBundle`; `fit_trees(dataset, splits, config) -> TreeBundle`; `raw_margins(bundle: TreeBundle, features: FeatureTable) -> np.ndarray`. Define both bundles in `contracts.py`.

- [ ] Resolve/pin Python 3.12 dependencies; implement the specified three horizons, initial settings, bounded challenger grid, early stopping, feature-family ablations, and both row-weight schemes.
- [ ] Serialize trials, best iteration, selected features/order, training weights, native models/tree dumps, environment, seed, and code revision. Missing labels, one-class fits, or absent real data fail clearly.
- [ ] Test excluded columns never enter matrices, training-only weighting/scaling, unchanged predictions after native reload, consistent tree count, and deterministic fixed-environment training tolerance. Test live training rejects fixture data.
- [ ] Run `python -m pytest tests/outage/test_train.py -q`; all pass. Tiny learnable fixtures verify mechanics only; do not use their scores as production validation.

### Task 7 — Global/regional calibration and complete explanations

**Files:** create `calibration.py`, `explain.py`, `configs/regions-v1.json`, `tests/outage/test_calibration.py`, `test_explain.py`.  
**Interfaces:** `fit_calibration(raw_margins, labels, region_ids, splits, config) -> CalibrationBundle`; `calibrate(bundle, raw_margin, region_id) -> CalibratedScore`; `explain(bundle: ModelBundle, row: FeatureRow) -> Explanation`. Define these records in `contracts.py`.

- [ ] Fit global and region-shrinkage challengers using only allowed calibration folds; record slope, intercept, offsets, λ, counts, boundary routing, and sparse/unknown-region fallback.
- [ ] Implement full native feature contributions and exact reconstruction through calibration, plus registry-based family sums and provenance.
- [ ] Test no held-out labels affect calibration, nonnegative slope, sum-to-zero offsets, stronger penalty shrinks offsets, sparse/unknown fallback, and degenerate calibration fails closed.
- [ ] Test raw and calibrated log-odds reconstruction within 1e-6; signed contributions and base terms survive reload; percentage-point and causal labels are absent from explanation templates.
- [ ] Run `python -m pytest tests/outage/test_calibration.py tests/outage/test_explain.py -q`; all pass.

### Task 8 — Evidence gates and per-cell support

**Files:** create `evaluate.py`, `support.py`, `configs/release-v1.json`, `docs/MODEL_CARD_TEMPLATE.md`; create `tests/outage/test_evaluate.py`, `test_support.py`.  
**Interfaces:** `evaluate(candidate, baselines, dataset, splits, config) -> ReleaseEvidence`; `fit_support(train_features, train_blocks, config) -> SupportBundle`; `assess_support(features, bundle, scopes, cutoff) -> SupportResult`. Define support records in `contracts.py`.

- [ ] Implement all metrics/gates from section 7, region-calibration comparison, block bootstrap, frozen scope construction, and transparent ineligible-slice reporting.
- [ ] Fit the support reference/threshold from training blocks only. Export region/utility/state/horizon/season coverage and held-out performance reports, plus grouped permutation/ablation evidence.
- [ ] Test a globally good model with a failing region cannot promote that region; small/one-class slices cannot pass; final test cannot alter thresholds/features; unlabeled states remain probability-ineligible despite plausible features.
- [ ] Test support distance, min/max, feature schema, incomplete inputs, and scope/horizon/season mismatch each yield the correct exclusion reason.
- [ ] Run `python -m pytest tests/outage/test_evaluate.py tests/outage/test_support.py -q`; all pass. Freeze the selected candidate, policy, and scope before a single final-test report.

### Task 9 — Bundle validation, three-window inference, and separate publication

**Files:** create `artifacts.py`, `predict.py`, `publish.py`, `tests/outage/test_predict.py`, `test_publish.py`.  
**Interfaces:** `save_bundle(bundle: ModelBundle, store) -> ModelRef`; `load_bundle(ref: ModelRef, store) -> ModelBundle`; `predict_batch(archive, bundle, policy) -> PredictionBatch`; `publish_batch(batch, store, expected_generation) -> PublicationResult`. Define `ModelRef` and reuse a documented store protocol, not an untyped import from the county service.

- [ ] Validate complete bundle hashes, feature/schema/static-vintage compatibility, all three models/calibrators, release evidence and live/fixture flags before inference. No runtime fitting or fixture fallback.
- [ ] Write probability, status, fallback COGs and feature Parquet under `outage/v1/runs/<batch_id>/`; include original weather and archive/model references, full timestamps, reason codes, validation scope, and explanation retrieval recipe.
- [ ] Apply exact window-wise max heuristic fallback; keep scores/probabilities as separate named artifacts. Reconstruct a sampled prediction from its persisted features and explanation.
- [ ] Test local supported cells show probability; unsupported/unvalidated cells have probability NoData and a separately labeled score; stale/invalid weather cannot become fresh fallback; rescoring old weather cannot reset freshness; outside-land cells stay masked.
- [ ] Test missing third window, corrupt model/object, model/feature order mismatch, race, retry, older batch, partial write, and rollback preserve the previous complete pointer. Verify the upstream score-only publisher still rejects probabilities.
- [ ] Run `python -m pytest tests/outage/test_predict.py tests/outage/test_publish.py -q`; all pass; benchmark full CONUS inference and record time/memory for scoring versus explanations.

### Task 10 — Commands, documentation, and operational handoff

**Files:** finish `cli.py`, create `Dockerfile.model`, `tests/outage/test_cli.py`; update `README.md` without overwriting upstream instructions.  
**Interfaces:** `python -m outage_model.cli` exposes `collect`, `archive-weather`, `build-dataset`, `train`, `evaluate`, `score`, `explain`, and `validate-batch`, each with explicit input/output references and test/live mode.

- [ ] Add offline demonstration, readiness audit, artifact inspection, explanation examples, dependency diagram, source/feature/model cards, and a statement that positive geographic results are limited to evaluated scopes.
- [ ] Keep the existing raster deployment independent. Define downstream invocation only after a complete weather publication; model failure cannot fail or mutate the weather batch.
- [ ] Document retention ownership/storage budget, source polling health, delayed label reconciliation, probability calibration drift, domain/hazard drift, stale model/weather, missing archive alerts, and rollback. Quarterly review is a proposed manual operating cadence, not a scheduled automation.
- [ ] Run `python -m pytest tests/outage -q`, then `python -m pytest tests/raster -q` in the compatible environment; document any environment-only native integration requirements. A fully offline end-to-end fixture must produce three windows, support reasons, inspectable trees, and reconstructible explanations.
- [ ] Produce a final readiness report separating software checks, available real labels/history, operational forecast backtests, local validation, and national-transfer evidence. A working pipeline alone does not pass a probability-release gate.

## 10. Prospective collection operations

The Cal OES adapter and `outage_model.cli collect` command are production-ready for continuous archiving. Raw snapshots (~100 KB each) and normalized event records are written to an immutable GCS bucket under `outage/v1/sources/california/`. Every snapshot is content-addressed; a repeated run with identical data is a no-op.

**Recommended collection infrastructure** (no server to manage):
- **Storage:** GCS bucket (e.g. `gs://see-your-power-outage`). Uniform bucket-level access; no object versioning. Apply a lifecycle rule deleting objects under `outage/v1/sources/california/raw/` after 90 days if cost is a concern; never apply lifecycle to `snapshots/` or `events/` — those are the durable label archive.
- **Scheduler:** GitHub Actions cron workflow (`.github/workflows/collect-caloes.yml`) running `*/15 * * * *`. Requires a GCP service account key stored as a repository secret (`GCP_SA_KEY`) with `roles/storage.objectAdmin` on the bucket. Free tier supports ~48 minutes/day of runner time; a 15-minute collect run uses ~30 seconds.
- **Source policy:** pass `--source-policy` only once per-utility type/onset/geometry audits are complete. Omitting it still archives the full raw snapshots and stores events with `outage_type='unknown'`; they will not qualify as training labels until the policy is set.

See `.github/workflows/collect-caloes.yml` and `outage_model/docs/OPS.md` for setup instructions.

## 11. Readiness sequence and unresolved external facts

1. **Now:** integrate with the completed DWD pipeline, pin a complete batch contract, and prioritize durable forecast archiving. Implement/test source adapters, labels, features, and model contracts using both explicit fixtures and verified upstream artifacts where available. Start continuous Cal OES collection immediately.
2. **As sources become usable:** collect prospective outage snapshots and weather archives; audit reporting domains, temporal gaps, onset/location semantics, and brief-outage detection. Investigate historical access independently.
3. **When aligned history exists:** build the real dataset, compare baselines and trees, test calibration, and report geographic/storm transfer. A recent forecast archive cannot be replaced by reanalysis for operational validation. Reconstructed hindcasts need explicit availability assumptions and remain separate from strict operational backtests.
4. **After untouched validation passes:** publish probabilities only in approved scopes; show the weather heuristic elsewhere. Expand scopes by new evidence, not by filling map gaps.

External facts that remain to be established, with executable failure behavior already specified: actual historical access/retention; event ID and onset reliability; complete utility reporting footprints; whether interval snapshots capture brief incidents; available seasons/storms/states; historical forecast availability; historical canopy releases. These are data-readiness constraints, not reasons to fabricate labels or silently loosen gates.

## 12. References

- [Existing hourly raster implementation plan](../docs/superpowers/plans/2026-11-16-hourly-weather-risk-rasters.md): upstream domain and score-only contract; its hourly supervised-target proposal is superseded **for this model** by the user's three 24-hour windows.
- [California dataset documentation](https://data.ca.gov/@california-office-of-emergency-services/power-outage-incidents): recent utility-map incidents, approximate point locations, 15-minute refresh, no historical archive, and county customer totals.
- [LightGBM parameters](https://lightgbm.readthedocs.io/en/stable/Parameters.html): training, reproducibility, regularization and constraint options. Pin actual installed versions during implementation.
- [LightGBM Booster API](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.Booster.html): native serialization, tree export and feature contributions including a reference term.
- [Watson et al., 2020](https://ietresearch.onlinelibrary.wiley.com/doi/10.1049/joe.2019.1274): multi-utility weather-outage modeling and environmental clustering; not proof of nationwide transfer.
- [Essus et al., 2026, preprint](https://arxiv.org/abs/2608.24665): outage-model performance can degrade under spatial and event holdouts; motivates realistic validation rather than random pixel splits.

## 13. Plan self-review

- Every confirmed decision maps to a contract and implementation task; CONUS scope and source-defined location meaning are explicit.
- Weather ingestion is a completed upstream producer with a checksum/schema adapter; model integration consumes its outputs without requiring changes to weather ingestion.
- Upstream retention risk has a durable-copy solution outside its GC namespace.
- Label completeness, multi-utility cells, uncertain point/onset semantics, overlapping windows/storms, calibration/explanation consistency, and stale/partial publication each have named acceptance tests.
- Regional calibration is optional and explicitly distinguished from pooled trees; no unearned national validation is inferred from it.
- All final-test decisions remain frozen; all release thresholds are disclosed engineering proposals; lack of evidence yields a weather score or unavailable status rather than an invented probability.
