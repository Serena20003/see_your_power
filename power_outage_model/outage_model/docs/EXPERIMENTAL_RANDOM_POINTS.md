# Experimental model trained from county totals and randomly allocated points

The split dataset contains 180,000 real parent groups / 1,440,000 proxy rows
across 2,758 represented counties. Actual fitting uses 121,940 train parents /
975,520 point rows from 2,474 counties. Other rows are tuning or held-out data.

This workflow implements the user's explicitly accepted approximation for the
two-day demo. **The historical county observations are real. Within-county
locations are simulated and are used during fitting.** It is separate from the
strict observed-incident model in `outage_model.cli`.

## Exact prediction meaning

The fitted regression estimates **allocated customer-outage impact conditional
on an available reported county observation**. Its output is a nonnegative
customer-equivalent weight per representative simulated point. Adding the eight
representative weights gives an experimental county impact estimate.

It does not estimate the probability of a new unplanned incident, physical
failure, household interruption or actual incident location. No point-level
geographic accuracy is established by county totals. All artifacts/inference
responses carry `experimental=true`, `locations_simulated=true` and
`physical_outage_probability=false`.

## Real source and missingness

- Source: ORNL/EAGLE-I authors' public Figshare article
  https://doi.org/10.6084/m9.figshare.24237376.v4
- API: https://api.figshare.com/v2/articles/24237376
- Initial fitted period: 2018, file `eaglei_outages_2018.csv`.
- Published file size: 999,210,542 bytes; published MD5:
  `726543e4a392e220108b035ba4c52e43`.
- License: CC BY 4.0; retain source authors and DOI in redistributed results.
- Raw schema: county FIPS, county/state names, customers out, UTC timestamp.
  County totals are customers/meters reported without power, **not incident
  counts**. Planned versus unplanned classification is not established here.
- Published metadata says zero values are omitted and omitted records conflate
  zero with collection gaps. Blank/nonfinite/negative counts are excluded. An
  absent observation is **never filled as zero**. Explicit numeric zeros, if
  present, remain real reported zeros.
- The daily aggregate is the maximum among available valid records in that UTC
  day. Partial reporting means this need not be the true full-day maximum.
  Received-record counts and source state-coverage metadata are retained.
- The source is read in ordered bounded HTTP byte ranges. Every range must match
  its declared position and length; the completed transfer must match the full
  published size and MD5, and its SHA256 is recorded. The national raw CSV is
  not retained or extracted onto the local drive. A truncated transfer does not
  promote a dataset reference. There is no generated-history fallback.

## Spatial allocation assumption

1. Use matching Census 2025 generalized 1:20m county polygons, read their `.prj`
   CRS, transform to EPSG:4326 and then project to CONUS equal-area EPSG:5070 for
   uniform sampling. Source and normalized CRS are recorded.
2. The support is explicitly **generalized county polygon including unclipped
   inland water**, not a verified distribution-service footprint or land-only
   mask. Alaska, Hawaii and territories are excluded from this CONUS version.
3. Using 2025 geography for 2018 is a retrospective boundary approximation. FIPS
   without a matching supplied geometry are excluded, not spatially guessed.
4. Allocate each actual county/day total `C` to eight uniformly sampled points,
   each with weight `C/8`. This preserves the reported parent mass. The maximum
   configurable count is 32 points; a customer is never declared an independent
   outage incident. Fractions are representative weights, not fractional people.
5. Seed 42 plus a stable SHA256 of county/date/horizon makes the positions
   reproducible and independent of CSV row order. The entire allocation rule is
   versioned. Sensitivity reports repeat held-out allocations with seeds 7 and 99
   while holding the fitted model fixed.
6. Uniform allocation assumes within-county locations are exchangeable. It
   ignores population, distribution assets, actual customer locations and true
   failure mechanisms. Random points do not add independent outage evidence.

## Time splits and group-disjoint observation counts

Forecast issue anchors advance every three days from January 1, 2018. Their
0–24, 24–48 and 48–72-hour target days are non-overlapping and cover all weekdays.
Each county/anchor/horizon observation is a real parent; proxies are generated
**after** assigning that parent's role.

- Train: anchors from January 1 to before September 1, with every target ending
  within that interval.
- Tune: September 4 to before November 1, with every target ending within it.
- Test: November 4 to before January 1, 2019.
- At least 72 hours separate the chronological roles; crossing anchors are
  excluded. No random point train/test split is used.
- County holdout: a deterministic 10% hash partition is excluded from all fitting
  and tuning. Only its later-period rows are used for geographic evaluation.
- A fixed parent cap of 180,000 keeps the CPU job bounded. If necessary, parent
  selection uses a uniform deterministic hash independent of outage count.
  All included counties, missing source observations and dropped groups are
  reported. The cap does not provide every county equally many observations.
- Distinct county/date/anchor parents, not generated points, are the group-disjoint
  observation units, not a claim of statistical independence. Long-running incidents/storms may still induce dependence;
  county counts do not provide the event IDs needed for perfect storm grouping.

The full three-day target for an anchor stays in one chronological role. Each
proxy receives training weight 1/8 so each real parent contributes total weight 1.
Native reload and data validation verify feature order, finite targets, parent
roles/index bounds and conservation of the source count.

## Predictors and fitting

The actual trained features are longitude, latitude, seasonal sine/cosine,
weekday sine/cosine, log county-polygon area and horizon-day index. Longitude and
latitude are intentionally admitted for this isolated experimental geographic
baseline; they are not added to the strict observed-incident feature registry.

**No weather or canopy feature is trained in this version.** Current 2026 DWD
forecasts and 2025 canopy are not misrepresented as contemporaneous 2018 inputs.
The model learns historical geographic/calendar associations under the random
allocation assumption. Current DWD weather-risk outputs remain separate.

Two small CPU LightGBM regression challengers use depths 3/5, leaves 7/15,
parent-balanced training, at most 200 rounds, early stopping 20, seed 42 and four
threads. They fit `log1p(allocated customer weight)` with absolute-error loss.
Tuning chooses weighted log-error; the later test never selects parameters.
This emphasizes typical conditional impact and can underestimate rare severe
outages. A train-only seasonal/county median baseline with pooled fallback is
reported alongside it. A worse tree result is reported rather than hidden.

Metrics distinguish simulated-point label agreement from error after summing
predictions back to real held-out county totals. Neither validates actual point
locations or unconditional county occurrence probability. Training-range flags
and a separate calendar-year extrapolation flag accompany inference. A 2026
example from a 2018-trained model is expressly extrapolative, not a validated
current operational forecast.

## Commands and portable handoff

Run from `power_outage_model/` in the existing Python 3.12 environment:

```sh
.venv312/bin/python -m pip install -r requirements-experimental.txt
.venv312/bin/python -m outage_model.experimental.cli inspect
.venv312/bin/python -m outage_model.experimental.cli prepare --year 2018
.venv312/bin/python -m outage_model.experimental.cli sample --inputs validation/experimental-real-inputs.json
.venv312/bin/python -m outage_model.experimental.cli train --dataset-ref validation/experimental-dataset-ref.json
.venv312/bin/python -m outage_model.experimental.cli validate --model-ref validation/experimental-model-ref.json
.venv312/bin/python -m outage_model.experimental.cli predict --model-ref validation/experimental-model-ref.json --date 2026-11-17 --counties all
.venv312/bin/python -m outage_model.experimental.cli export --model-ref validation/experimental-model-ref.json
```

Arguments above require spaces between option and value, e.g. `--year 2018` and
`--date 2026-11-17`. Ref files contain the exact checksum object printed under
`dataset_ref` or `model_ref`; `prepare` prints the input-reference mapping.
Final local handoff paths and commands are recorded in
`validation/experimental-point-status.md` after the real run.

The export ZIP includes native model text, model manifest, county geometry,
evaluation report, model-reference handle and minimal readme under preserved
object keys. It excludes the raw national CSV and training arrays. Load it with
this exact Python package and locked model dependencies plus pyshp 2.3.1. It
requires no runtime fitting or remote source fetch. Predictions are written to
`outage/experimental-random-points/v1`, never to the strict probability pointer.

A model-directory-wide 10 GB hard write budget checks atomic staging headroom.
No user data is automatically deleted. The 5 GB preferred target is already
exceeded by prior weather/static downloads; this workflow keeps only compact
historical aggregates, bounded sampled training data and small native artifacts.
No Docker image, external deployment, frontend change or cloud resource is made.

The experimental API uses **fixed UTC calendar-day windows**, starting at
00:00 UTC on `--date`. It returns exact UTC window starts/ends. These are not
silently described as rolling 24-hour windows starting at the current clock time.

## Observed first-run limitation

The first genuine 2018 fit estimates **typical/median-like conditional impact**,
not an unbiased conditional mean. On the later-time held-out groups, mean
predicted county impact was 20.65 customer-equivalent units versus 230.62 reported,
and total predicted/observed mass was 0.0895. Rare severe observations dominate
reported totals. Aggregate MAE was 222.72 versus 227.21 for the seasonal/county
median baseline; RMSLE was 1.909 versus 2.012. These modest improvements do not
establish a reliable severe-outage model. The test was inspected only after
candidate selection; it was not used to retune this fitted artifact.

For a portable bundle extracted elsewhere, point the same package at that
self-contained object store:

```sh
.venv312/bin/python -m outage_model.experimental.cli --store /path/to/extracted/output --budget-root /path/to/extracted validate --model-ref /path/to/extracted/model-ref.json
.venv312/bin/python -m outage_model.experimental.cli --store /path/to/extracted/output --budget-root /path/to/extracted predict --model-ref /path/to/extracted/model-ref.json --date 2026-11-17 --counties 06037,48201
```
