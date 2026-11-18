# Experimental random-point baseline: actual result and demo handoff

Completed November 17, 2026 (America/Los_Angeles). A requested subagent executed
`EXPERIMENTAL_POINT_PLAN.md`. This is a **genuine fitted experimental model**
using real historical county observations and explicitly simulated locations.
No frontend or external deployment was performed.

## Actual fitted model

- Model ID: `4e97b613a2ff4aebeb7d63d4140c397972c2c832983611ac099a7db2d0d0d572`.
- **Actual fit:** 121,940 real county/date/horizon parent groups; 975,520 simulated
  weighted point rows; 2,474 training counties.
- **Entire split dataset:** 180,000 parent groups; 1,440,000 point rows; 2,758
  represented CONUS counties. Tuning and held-out rows were not fitted.
- Eight reproducible uniform equal-area polygon points per parent; reported
  customer mass is conserved through weights. These are not independent outage
  incidents, and different parent groups can still share storms/ongoing impacts.
- Target: typical/median-like allocated customer impact conditional on an
  available reported county-day observation. No physical-outage probability,
  actual incident-location accuracy or new-unplanned incident classification.
- Predictors: longitude, latitude, seasonal/weekday sine/cosine, log county
  polygon area and horizon day. **No weather or canopy features were trained.**
- Two bounded shallow LightGBM challengers, chronological tuning, 200-round maximum,
  seed 42, four CPU threads. Real fitting/evaluation took about 42.6 seconds after
  data preparation. Native model reload predictions are identical.

## Real source and compact preparation

Source: [ORNL/EAGLE-I Figshare v4](https://doi.org/10.6084/m9.figshare.24237376.v4),
CC BY 4.0. The full 2018 CSV transfer was verified at 999,210,542 bytes against its
published MD5 `726543e4a392e220108b035ba4c52e43`; a SHA256 is retained too.

Of 21,776,806 source rows, 2,027,828 blank counts were excluded. The streamed source
produced 666,160 observed county-days from 2,815 raw counties. The compact historical
Parquet is 1,726,892 bytes. The raw national CSV was never retained on local disk.
Missing county observations were never imputed as zero. Published state coverage
metadata is retained, but is not treated as proof of whole-county ascertainment.

Census 2025 generalized county geometry was explicitly transformed from EPSG:4269
to EPSG:4326, then sampled in equal-area EPSG:5070. Support is a generalized county
polygon including unclipped inland water, not a verified land-only/service-area
footprint. Using 2025 boundaries with 2018 data is a disclosed retrospective
approximation. Unmatched FIPS are excluded.

## Held-out results and limitations

| Evaluation | Parent groups | Counties | Model aggregate MAE | Seasonal/county baseline MAE |
| --- | ---: | ---: | ---: | ---: |
| Later-time test |25,511 |2,321 |222.72 |227.21 |
| Entire-county holdout, later time |3,084 |269 |356.24 |357.31 |

The later-time RMSLE was 1.909 versus 2.012 for the baseline. These are conditional
observed-county count errors, not incident-location validation. Randomly generated
point-label agreement is separately reported. Parents are split-disjoint, not
statistically independent; no independent-storm confidence interval is claimed.

**Important:** the log-absolute-error model estimates typical/median-like impact
and strongly underpredicts severe totals. On later-time test groups the mean
predicted aggregate was 20.65 versus 230.62 reported; total predicted/observed mass
was 0.0895. The test was not used to retune after seeing that limitation. Treat the
artifact as an experimental baseline, not a reliable severe-outage predictor.

Fixed held-out allocation seeds 7 and 99 were also evaluated without refitting;
their aggregate MAEs were 223.40 and 223.48 on the same 2,000-parent sensitivity set.
The full evaluation manifest records both useful and unfavorable results.

## Ready-to-use outputs

- Portable inference-only ZIP:
  [`artifacts/experimental-point-model-4e97b613.zip`](../artifacts/experimental-point-model-4e97b613.zip).
  It is about 0.88 MB and includes the native model, model reference, county
  geometry, evaluation, assumptions and compact real-source/license provenance.
  It excludes the national CSV and training matrices.
- Model handle: [`experimental-model-ref.json`](experimental-model-ref.json).
- Full supported-scope prediction handle:
  [`experimental-nationwide-prediction-ref.json`](experimental-nationwide-prediction-ref.json).
- Assumptions and exact target:
  [`EXPERIMENTAL_RANDOM_POINTS.md`](../outage_model/docs/EXPERIMENTAL_RANDOM_POINTS.md).
- Machine-readable complete status:
  [`experimental-point-status.json`](experimental-point-status.json).

The prediction export contains 8,274 county-window records / 66,192 representative
points for 2,758 supported CONUS counties. It explicitly lists 351 supplied county
geometries without model source support. It is not full-US or nationwide-validated
coverage. The 2026-11-17 example is calendar/geographic extrapolation from 2018;
that flag is present in the output, along with experimental/simulation flags and
training-feature-range indicators. Its windows are fixed UTC days, with explicit
start/end timestamps, not rolling windows starting at the current clock time.

From `power_outage_model/`, using the existing environment:

```sh
.venv312/bin/python -m outage_model.experimental.cli validate --model-ref validation/experimental-model-ref.json
.venv312/bin/python -m outage_model.experimental.cli predict --model-ref validation/experimental-model-ref.json --date 2026-11-17 --counties all
```

To use only the portable ZIP elsewhere, extract it and point this same package
at its object store. No training data or download access is required at runtime:

```sh
.venv312/bin/python -m outage_model.experimental.cli --store /path/to/extracted/output --budget-root /path/to/extracted predict --model-ref /path/to/extracted/model-ref.json --date 2026-11-17 --counties 06037,48201
```

## Verification and storage

- 12 experimental checks passed; 26 checks passed in the final focused suite,
  including unchanged strict-model contract/training/calibration and raster
  window regressions. See `experimental-final-tests.log`.
- Real native reload parity passed. The real export was loaded into a fresh
  temporary object store without training matrices; API predictions matched
  exactly and the portable CLI validated. See `experimental-portable-check.json`.
- Full-scope `predict --counties all` completed successfully; its JSON command
  result is `experimental-predict-cli-result.json`.
- Compilation and `pip check` passed. Only pyshp 2.3.1 was added for Census source
  reading; strict model/raster dependencies were not upgraded.
- The model directory is approximately 8.30 GB, below the 10 GB hard ceiling. The
  pre-existing weather/static downloads already exceeded the 5 GB preferred target.
  No user files were deleted and no extra Docker image was built. Shared external
  Docker caches and system test temporaries are outside this directory measurement.

The strict observed-incident workflow and its probability-release gates remain
unchanged. This separate experimental workflow uses simulated labels only under
the user's explicit authorization, with assumptions retained alongside the model.
