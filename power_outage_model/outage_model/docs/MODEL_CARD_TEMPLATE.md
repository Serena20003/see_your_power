# Outage model card — fill from a real evaluated release

- Candidate/model ID, artifact checksums, code/environment and feature registry:
- Training/calibration/gate/final-test dataset IDs and exact UTC dates:
- Prediction: at least one new explicitly classified unplanned reported incident
  marker in a fixed cell during each of three half-open 24-hour windows.
- Scope: approved exact cell footprint, reporting-domain union, horizon, seasons
  and supported hazards. CONUS weather coverage does not establish CONUS skill.
- Exclusions: planned/PSPS/unknown classifications; incomplete coverage; uncertain
  identity/onset/geometry; immature labels; retrospective static vintages.
- Sources: history-access, rights, reporting omission and brief-outage audits;
  label lag/freeze, late revisions, land and canopy pins and attribution.
- Interpretation limits: source-defined approximate incident markers; geographic
  cells have unequal land area; not asset failure or household/county probability.
- Models: seasonal Beta(1,1), regularized logistic and three CPU LightGBM treesets;
  bounded trials, weighting, selected feature-family ablations, best iterations,
  native tree exports and development geographic/storm transfer experiments.
- Calibration: global slope/intercept, candidate regional offsets and penalty
  trials, counts, sum-zero constraint, sparse/unknown fallback and gate decision.
  Regional offsets are penalized MAP estimates, not a Bayesian posterior.
- Evaluation: Brier/log loss, both baseline skills, event-block bootstrap, PR-AUC
  with prevalence, reliability intervals/counts, disclosed alert thresholds,
  macro utility/state/event metrics and supported/unsupported hazard slices.
- Support: train-only robust scaling, min/max and 99th-percentile independent-
  block nearest-reference distance; reference keys/hash and maximum 10,000 rows.
- Explanation: expected raw-margin reference, every signed native contribution,
  calibration terms, exact reconstruction, grouped summaries and saved features.
  Contributions are log-odds associations, not additive percentage points.
- Failure modes: omitted or short incidents, missing distribution asset exposure,
  privacy-shifted markers, ID reuse, canopy/weather bias, unseen storms/geography,
  incomplete forecast availability and drift. Quantify boundary sensitivity.
- Release decision: gate and one untouched final report; no threshold changes
  after final-test consumption. Unsupported cells show a separately named peak
  hourly weather risk score. Stale/missing weather is unavailable.
- Operations: owner, resource/retention budget, health/drift checks, rollback,
  stale model review, quarterly manual review and incident response.

No real model card is claimed by the checked-in tests. Test-only generated data
must never appear as operational validation or geographic-transfer evidence.
