# Implementation and validation status

All implementation deliverables are under `power_outage_model/`. Existing
`model/` source code, service behavior, training logic and frontend were not
changed for this pipeline. The repository contained unrelated concurrent edits;
those files were left out of this work.

Historical package-wide and existing-app test counts below describe the shared
working tree at the time of those checks. They can include separate outage-model
or app work that is not part of these raster commits. Commit verification exports
only the indexed raster files into an isolated directory, so the raster tests do
not depend on those unrelated changes. The native raster suite contains 68 tests.

## Current retention policy

Forecast retention is now **latest complete + one previous complete batch**,
with automatic cleanup after publication. Superseded complete batches are not
kept for seven days. Both retained batches' processed lookback, provenance and
static dependencies remain protected at any age; shared weather files outlive
their original batch only while one of the two still needs them. Failed or
unpublished candidates cannot replace the recovery reference. Incomplete staging
files and small attempt records retain a seven-day retry/cleanup grace period.
Raw weather archiving remains disabled. Local working caches are separate.

Verification: **68 raster tests passed in Docker**, including native CDO;
package-wide local checks: **80 passed, 1 native-tool skip**. Both suites reported
two upstream deprecation warnings. The eight focused retention tests cover
rotation, shared dependencies, interrupted cleanup, stale publishers, failed
updates, legacy pointers and cleanup-error reporting. The existing output store
was inspected with a dry-run only; its files were not deleted. Details are in
[two-batch-policy-checks.json](two-batch-policy-checks.json).

## Previous storage policy update (superseded batch-history rule)

The 30-day archive was removed at the user's request. New runs and the cloud
wrapper no longer copy raw GRIBs to the output store. Cleanup can remove legacy
raw archive objects immediately; processed batches and metadata retain the
existing seven-day protection, and the latest good batch remains protected at any
age. Cloud cache persistence now stores only observation metadata and preserves
first-seen timestamps across matching re-downloads. Optional undeployed GCS
configuration disables version history/soft-delete buffers. Existing local data
and working caches have not been deleted. A free cloud storage adapter and a
zero-cost execution design are not established by this retention change.

Updated-policy verification: **63 tests passed in Docker**, including native CDO;
local verification: **62 passed, 1 intentional native-tool skip**. Both runs had
two upstream deprecation warnings. Terraform validation passed. A dry-run cleanup
preview marked legacy raw objects for deletion without deleting them.
See [retention-policy-checks.json](retention-policy-checks.json).

## Verified (initial implementation)

- Final container suite: **59 passed**, including native CDO. Local suite:
  **58 passed, 1 skipped** (CDO intentionally runs in Docker). Two upstream
  Rasterio/Affine pending-deprecation warnings remain; the NetCDF ABI warning
  was eliminated by using SciPy NetCDF I/O.
- Offline real-format GRIB fixtures produce exactly 72 three-band weather COGs
  and 72 matching one-band environmental-score COGs, plus internal history.
- Tests cover source/cutoff eligibility, missing hourly cadence/history, units,
  precipitation resets, static area aggregation and California clipped route
  exposure, exact risk formula, COG geometry/metadata, incomplete/corrupt batches,
  immutable objects, compare-and-swap pointers, retention, crash retry and replay.
- Dedicated Docker image includes native ecCodes, CDO, GDAL and pinned Python
  dependencies. Its required native integration test exercises actual CDO.
- Existing Python suite: 57 passed (one upstream deprecation warning), using
  bytecode/cache-disabled execution so verification did not write test caches.
- Terraform 1.9.8 init/validate and formatting checks pass. No deployment.
- A live ICON single-interval check covers all 98,176 default-domain cells in
  all three weather bands. The checked-in JSON records inputs, checksums,
  native geometry, methods, versions and measured results. Successful rerun:
  20.33 seconds with decoded sources and conservative/triangular weights cached; 12,755,676 bytes decoded weather inputs; 1,787,175,612-byte
  native geometry; parent peak RSS 263,656 KiB and largest child 1,086,772 KiB.
  These are separate process peaks, not a sampled total-memory maximum. Docker
  had four CPU slots and a 3.824-GiB memory limit. The original conservative
  weight generation is excluded from the 20.33-second successful rerun.

## Still required before production acceptance

- Download and checksum exact NLCD canopy, authoritative CONUS land-only
  boundary, California boundary/assessment and CEC transmission exports; complete
  the static template and verify the product's exact special-value codebook.
- Spot-check real coastal, forest, urban and state-border static cells.
- Run and record a complete **72-hour live** batch with that real static release;
  measure download volume, all-stage wall time, total peak RAM and storage on
  the intended 4-vCPU/8-GiB target. The plan's <30-minute budget is unproven.
- Deploy only after review; smoke-test real GCS IAM, scheduler invocation,
  competing publishers and actual alert delivery. Terraform schema validation
  is not a cloud execution test.

These are unverified acceptance items, not passing fixtures presented as live
results. The default live configuration fails closed while static pins are absent.

## Review decisions

- User-directed path change: every plan path was moved from `model/` to this
  standalone directory. Existing integrations must opt into the new package.
- Work stayed on a separate `codex/hourly-weather-risk` branch in the current
  checkout, preserving unrelated edits; this provides less isolation than an
  additional worktree.
- Availability uses first successful observation rather than guessed historical
  availability. A new poller can reject an otherwise previously available run;
  it cannot accidentally use hindsight.
- Actual CDO rejected native bilinear ICON remapping. Temperature and geographic
  U/V now use explicitly versioned triangle barycentric interpolation in CONUS
  Albers; rain uses conservative remapping. The interpolation change needs
  scientific evaluation, and is never advertised as outage-model calibration.
- Real static accuracy/full live performance and deployed cloud behavior remain
  acceptance checks above. Calibrated probabilities, frontend/tile integration
  and unrelated `model/` edits remain outside this package's scope.

Deferred minor review findings: per-attempt local scratch needs manual cleanup;
the cloud wrapper currently selects the verified v1 endpoint directly, while
local catalog commands support both configurable directory adapters.
