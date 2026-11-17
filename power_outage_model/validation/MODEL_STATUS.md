# Model training implementation and readiness

Updated November 16, 2026 (America/Los_Angeles).

The downstream training/evaluation/scoring software is implemented. **No
production model has been trained, no probabilities released and no deployment
performed.** The checked-in generated examples and models are confined to tests.
Current real data does not meet the plan's training or release conditions.

## Implemented

- Frozen target/source/label/model/output contracts; content-addressed durable
  forecast dependency copies and replay outside upstream GC. Feature replay is
  distinguished from normalization reproducibility; missing raw/remap assets
  prevent live training. Dependency closure is rechecked when loading archives
  and admitting real datasets to training.
- Pinned real California incident schema; bounded read-only paging, raw evidence,
  conservative classification/onset/geometry defaults and no invented reporting
  completeness. Indexed event reconciliation, local quarantine, boundary
  sensitivity, whole-cell/time coverage, maturity and immutable label freezes.
- Shared registered 17-feature transform, land-area neighborhoods, explicit
  availability/static-vintage checks, generated feature documentation and
  compressed Parquet datasets with exclusion/lineage records.
- Five exact chronological roles, 72-hour embargo, overlapping-target/storm/
  incident purges and geographic holdouts. Evaluation is bound to the exact
  trained dataset and split, not an editable role reassignment.
- Seasonal Beta(1,1) and standardized logistic baselines; three deterministic
  native LightGBM classifiers; bounded trials, early stopping, weight schemes,
  geographic selection and feature-family ablations. Discarded native candidate
  models are not all retained on disk.
- Nonnegative sigmoid/global and sum-zero penalized regional calibration,
  chronological penalty selection and separate gate comparison. Full signed
  contributions reconstruct both raw margin and calibrated log-odds.
- Train-only support references, min/max and independent-block distances;
  baseline/reliability/uncertainty metrics; conservative geographic/hazard gates;
  typed identity-bound development/audit reports and numerical response curves
  with feature-support marks. Missing/sparse slices cannot gain public coverage.
- Separate three-window probability/status/peak-hourly-score COGs, persisted
  features, on-demand explanations, complete output reconstruction, original
  weather timestamp binding, conditional publication, retry/order protection and
  explicit rollback. Fixture pointers are isolated under `outage/v1/testing/`.
- Commands, Python 3.12 model dependency lock, CPU-only Dockerfile and operational
  instructions. Final-test dates are atomically consumed and cannot be reused
  by renaming the dataset. Local writes share an atomic storage-budget check.

## Observed real data

The live Cal OES collection retrieved **96 current records**, with the pinned
schema, stable complete object-ID paging and a fresh source timestamp. Raw pages
and normalized records are retained in the local output store. The receipt is
[model-source-check.json](model-source-check.json).

This is one prospective snapshot, not an archive of historical new unplanned
onsets. There are **zero audited coverage intervals, zero eligible real datasets
and zero complete live weather archives** in the model store. The default
collector does not promote `Not Planned`, reported-system time or approximate
markers to audited labels. No ODIN historical access is assumed.

The existing real DWD native check covers a single interval. It is not a
complete 72-hour live batch with production land/canopy pins. A representative
complete **test-only** batch contract is pinned in `fixtures/outage/`; archive
integration tests reproduce all 72 intervals and survive upstream deletion.

## Verification

The combined model/raster suite passed **124 tests, with one native-CDO skip**
and two existing Rasterio deprecation warnings. Two tests added afterward also
passed in focused checks (13 label/command checks and four command checks).
The current inventory is **59 model checks and 67 raster checks passing**, with
one raster check skipped. `pip check`, module compilation and scoped whitespace
validation passed. See [model-software-checks.json](model-software-checks.json). Focused checks
include native reload/determinism, regional shrinkage, schema/fixture exclusion,
late labels, leakage purges, explanation reconstruction, supported and fallback
outputs, stale weather, CAS/rollback/corruption, atomic storage quotas and final
period consumption. The offline test trains/calibrates all three native models
and produces inspectable three-window output entirely within test isolation.

[model-cpu-benchmark.json](model-cpu-benchmark.json) records native inference and
full-contribution mechanics for 98,176 generated **test** rows per horizon. It
excludes weather/archive I/O, area/feature extraction and support queries, and
uses small test models. It does not establish full production CONUS latency,
memory or predictive performance.

## Storage and deployment limits

[model-readiness.json](model-readiness.json) records about **3.12 GB** under the
local model directory, including its existing environments and weather caches.
The default write budget is 5 GB; configurations above 10 GB are rejected.
Content addressing, compressed tables, no bulk backfill and on-demand
contributions limit growth. No existing user data/caches were deleted.

The directory measurement excludes pre-existing Docker images/shared build
caches, system temporary test storage and remote buckets. No new Docker image
was built. Operators must include these external allocations in a total local
footprint and use a durable remote store for long real forecast history. A
model-only dependency closure and Dockerfile are supplied; target-container,
cloud IAM/lifecycle, scheduler and actual runtime smoke checks are not claimed.

## Decisions and tradeoffs

1. Preserve normalized feature replay when raw inputs are unavailable, but mark
   normalization non-reproducible and reject live training. Cost: real fitting
   waits for a complete durable raw/remap dependency closure.
2. Keep work confined to `power_outage_model/` on the existing working branch and
   preserve unrelated edits. Cost: less isolation than a separate clean worktree;
   scope and changes were reviewed without staging unrelated files.
3. Fail a proposed scope with unsupported required slices. Cost: more history may
   be needed than a pooled score alone would suggest; coverage is not invented.
4. Avoid another local image/build cache while storage is constrained. Cost:
   actual target-container/CI validation remains necessary before deployment.

A fresh read-only reviewer identified ten important integrity/readiness issues;
the implementation addresses them with regression checks. A minor remains:
some compact statements/dynamic imports could be reformatted for readability.

## Remaining acceptance work requires real evidence

- Pin and verify real canopy/land static release availability and complete live
  weather batches, preserving all raw/native/remap dependencies remotely.
- Obtain real typed incident history with stable IDs, qualified marker/onset
  semantics and audited complete utility-union footprints/poll omissions,
  including brief-outage ascertainment and source rights.
- Accumulate independent seasons/storms and aligned operational forecasts; make
  reviewed event blocks and exact splits, train on real data, run development
  transfer/audit jobs and freeze scopes/policies.
- Consume one genuinely untouched future test period and pass every declared
  scope/horizon gate before any public outage probability.
- Benchmark complete real CONUS scoring/explanation jobs in the intended target
  environment and verify deployment/recovery/alert behavior.

Software execution and current-map access do not satisfy these data/scientific
acceptance requirements. There is no synthetic substitute or toy deployment.
