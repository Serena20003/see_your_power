# Hourly weather and environmental risk raster pipeline

This standalone package implements the hourly raster plan. All new implementation,
fixtures, tests, container files and infrastructure live here. The existing
`model/`, county API and frontend are independent and unchanged by this package.

The output is an **uncalibrated environmental score**, not an outage or equipment
failure probability. California transmission exposure is a separate inspectable
static layer; it never changes the common CONUS score.

## Run the offline demonstration

Use Python 3.12, or the dedicated container. Run these commands from
`power_outage_model/`:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -m pytest -q
.venv/bin/python -m raster_pipeline.cli make-fixture --output .cache/fixture
.venv/bin/python -m raster_pipeline.cli run \
  --config raster_pipeline/configs/conus-v1.json \
  --cutoff 2026-11-16T06:00:00Z \
  --fixture-dir .cache/fixture --work-dir .cache/work --output output
```

This explicit fixture mode uses tiny, locally generated, real GRIB2 messages and
CC0 synthetic static data. It produces **72 weather COGs and 72 risk COGs** on a
two-cell test grid, plus supporting lookback inputs. Nothing automatically falls
back to fixtures. Every fixture manifest is labeled and cannot be mixed with live
static releases. `--dry-run` checks selection and static coverage without publishing.

```sh
docker build -t see-your-power-raster:dev .
docker run --rm see-your-power-raster:dev python -m pytest tests/raster -q
docker run --rm -v "$PWD:/pipeline" see-your-power-raster:dev \
  python -m raster_pipeline.cli make-fixture --output .cache/fixture
docker run --rm -v "$PWD:/pipeline" see-your-power-raster:dev \
  python -m raster_pipeline.cli run --config raster_pipeline/configs/conus-v1.json \
  --cutoff 2026-11-16T06:00:00Z --fixture-dir .cache/fixture \
  --work-dir .cache/work --output output
```

The image pins Python 3.12.8 by digest, CDO 2.1.1, Debian ecCodes 2.28.0 and GDAL
3.6.2. `requirements.lock` records all resolved Python dependencies, including
Python ecCodes 2.48.0 via eccodes 2.44.0, rasterio 1.4.3 and scipy 1.14.1.
The Docker build runs the offline suite and requires the native CDO integration
test. Run `rio cogeo validate <file.tif> --strict` inside the image for standalone
COG validation; publication performs that validation itself for every target file.

## Prepare real static inputs

The hourly path requires a pinned static release. The provided
`raster_pipeline/configs/static-v1.json` is a **preparation template**, not a
claim that source archives have been downloaded or verified. Its missing paths
and checksums deliberately stop production builds. Set each local path, exact
SHA-256, vintage, source URI, attribution and license after obtaining the source:

* NLCD percent tree canopy CONUS reference year 2025, edition v2025-6 (released
  2026), from the USDA/MRLC catalog. Do not substitute the Science TCC,
  rangeland tree cover, forest class or canopy height product. Check the extracted
  edition's codebook. Only 0–100 is valid; special and missing values are excluded.
* An authoritative **land-only CONUS** footprint, with its boundary and land/water
  preparation provenance; gross state polygons including water are insufficient.
* A California boundary, an explicitly assessed pilot footprint, and a pinned CEC
  transmission GeoJSON export. GeoJSON inputs use EPSG:4326. CEC catalog attributes
  include `Status`, `Type` (`OH`, `UG`, `UW`, null), `Owner`, `kV`, and `Circuit`.
  Only operational lines qualify; unrecognized statuses remain in inventory JSON.
  UW/unspecified type is retained in the unknown-type band rather than guessed UG.

```sh
python -m raster_pipeline.cli build-static \
  --config /absolute/path/to/completed-static-config.json --output output
```

Canopy aggregation reads bounded windows and computes physical overlap areas in
an equal-area CRS. It supports source WGS84 geographic pixels or an existing
north-up equal-area raster, preserving real zero canopy and the valid land area
fraction. The environmental coverage threshold is 95%. Risk is NoData outside
analysis land or when required environmental inputs are missing. Transmission
route intersections use California Albers meter lengths, deduplicate overlaps,
and preserve assessed zero versus outside/unassessed NoData. Conflicting coincident
types use documented OH→UG→unknown precedence; original attributes are retained.
The assessment/boundary footprints are supporting static artifacts. A release
version is immutable, and a grid ID may never be reused with different geometry.

## Poll and run live weather

The current observed route is
`https://opendata.dwd.de/weather/nwp/v1/m/icon/p/{FIELD}/r/{RUN}/s/PT{STEP}H00M.grib2`.
The legacy `icon/grib/{cycle}/{field}/...grib2.bz2` layout is also supported through
`routes-legacy.json`. Candidates from directory listings are decoded and verified;
filenames never establish scientific metadata or hourly availability.

Supply the DWD ICON geometry matching UUID
`a27b8de6-18c4-11e4-820a-b5b098c6a5c0`. The observed full geometry SHA-256 is
`3a8bf7d1b84069c6d9e71249dd61a3f2e9c974f743ec5adfdf213bc6333bfdc1`
for the decompressed `icon_grid_0026_R03B07_G.nc` (1,787,175,612 bytes).
Use the DWD source at `https://opendata.dwd.de/weather/lib/cdo/`.
A superficially similar filename or precomputed nearest-neighbor EASY weights
are insufficient. Add its absolute path/checksum to your live configuration and
set `static_manifest` to the output-store object key
`raster/v1/static/<version>/manifest.json`.

```sh
# Freeze observed metadata before selecting a cutoff. Can download several GB.
python -m raster_pipeline.cli catalog \
  --routes raster_pipeline/configs/routes-v1.json \
  --cache-dir .cache/raw --output .cache/catalog.json
# Set source_catalog to the absolute catalog path in your live config.
python -m raster_pipeline.cli run --config /absolute/path/to/live.json \
  --cutoff <explicit-UTC-cutoff> --work-dir .cache/work --output output
# Or poll, then freeze cutoff at actual observation time:
python -m raster_pipeline.cli poll-run --config /absolute/path/to/live.json \
  --routes raster_pipeline/configs/routes-v1.json --work-dir .cache/work --output output
```

First-seen means the successful observed GRIB retrieval, not initialization or
HTTP Last-Modified. A new installation cannot backdate availability. An exactly
aligned cutoff targets that hour; otherwise the target starts at its UTC ceiling.
The newest eligible run must contain real hourly boundaries through the final
72nd interval. Sparse later leads are never interpolated in time. Earlier-run
lookbacks are normalized within each run before assembly. Missing history,
invalid precipitation resets, remapping holes or canopy gaps fail the attempt
and preserve the previous latest pointer.

**Native interpolation correction:** CDO cannot apply `genbil` to ICON's
unstructured triangles. The live integration check established that failure.
For this implementation, precipitation uses conservative CDO remapping and
Float64 intermediates; native ICON temperature and geographical U/V use cached
Delaunay triangle barycentric weights in EPSG:5070, then wind speed is derived.
Regular lat/lon test sources still use bilinear CDO interpolation. The recipe,
geometry checksum, tools, projection and actual weight checksums are archived.
This is an explicit engineering change from the plan's infeasible native bilinear
instruction, not a validated outage-model relationship. DWD's documented zonal /
meridional wind metadata and known product UUID are required when template 101
omits vector-reference flags; unknown products fail explicitly.

## Files, replay and recovery

The default target arrays contain about **108 MiB per batch before compression**
(98,176 cells × 72 hours × 4 numerical bands × 4 bytes). Masks, overviews,
metadata, history, raw GRIB cache, remap weights and static sources add overhead.
The pinned native geometry alone is 1.79 GB; discovery can download several GB.
Measure retained storage with real sources before sizing a production bucket.

The default grid is the rectangular EPSG:4326 extent [-125,24,-66,50], 0.125°,
472×208. The rectangle includes ocean and neighboring countries; weather is
complete over the rectangle and risk uses the separately versioned CONUS land
mask. Files are tiled 256×256, DEFLATE COGs, Float32 with NoData=-9999, nearest
overviews. Coverage is UInt8 with NoData=255. NoData never means zero.

`raster/v1/latest.json` points to an immutable run manifest and records one
`previous` manifest reference for recovery. That pair changes atomically only
after a complete batch passes validation; identical retries do not rotate it. Each of the 72 ordered
entries supplies exact `[start,end)` UTC bounds and weather/risk keys, SHA-256,
bytes and per-band valid-cell counts. Weather bands are interval-total rain mm,
endpoint temperature °C and endpoint wind speed m/s. The manifest separates
initialization, cutoff, source observation, target and publication timestamps;
records the exact grid, static checksum/vintage/coverage, risk configuration and
recipe; and declares `heuristic_score`, `calibrated=false`, `asset_conditioned=false`.

`inputs.json` retains source URLs, levels, steps, grid/packing metadata,
first-seen/retrieval times, raw object checksums, tool versions and remap identities.
Raw GRIBs stay in the processing cache and are not uploaded to the output store.
There is no 30-day raw archive. Supporting lookback
weather files allow independent recalculation. Diagnostics summarize every
hour's base score, canopy contribution, drivers and cumulative rainfall.

```sh
python -m raster_pipeline.cli recalculate \
  --weather-manifest raster/v1/runs/<batch-id>/manifest.json \
  --risk-config /absolute/path/to/new-risk.json --output output
# Optional: --static-manifest raster/v1/static/<new-version>/manifest.json
python -m raster_pipeline.cli gc --output output          # dry-run deletion list
python -m raster_pipeline.cli gc --output output --apply  # reference-aware retention
```

Batch identity includes source content, target, grid, lookback, static release,
risk configuration and the versioned weather/geometry/tool recipe. Poll cutoffs
within the same target interval do not duplicate a batch. A retry reuses the
first immutable input inventory after interruption. New risk configurations
create separate immutable manifests while reusing archived weather objects.

All 144 target rasters and the pinned static coverage are independently checked
before a manifest/pointer is published. Local writes use locks, fsync and atomic
replacement; GCS uses generation preconditions. An older or losing publisher
cannot displace a newer completed batch. GC shares a reference lock with
publication. The retention policy is **latest complete batch + one previous
complete batch**, regardless of their age. After publication, automatic cleanup
removes older complete batches immediately. Both retained batches keep their
weather/risk rasters, input provenance, diagnostics, supporting lookback and
static dependencies. Shared weather objects remain protected even if their
original batch folder has otherwise been retired; they are removed when neither
retained batch uses them. Small retirement records track that shared ownership.

There is no seven-day forecast-batch archive or 30-day raw archive. New runs never
upload raw GRIBs, and cleanup removes legacy raw archive copies immediately.
Unfinished staging files, unused static releases and small attempt records have
a seven-day retry/cleanup grace period; they cannot become recovery batches.
Local working caches are separate and still require manual cleanup. Risk-only
replay uses retained processed weather; older full weather reconstruction is no
longer guaranteed. A pre-existing pointer without a recovery reference can use
an older batch only after that batch passes the complete validation checks.

Cleanup reads the pair under the publication/reference lock. If cleanup fails,
the new validated batch remains published and the error is logged for retry;
cleanup never reports an already-published valid batch as an incomplete forecast.

The optional GCS configuration disables object versioning and soft-delete history
so removed objects do not create a hidden retained archive. This configuration
has not been deployed. It does not establish a free cloud service; the prepared
Cloud Run job must not be deployed under a zero-cost requirement without a
separate approved design. No existing local or cloud data was deleted by this
policy change. Local `.cache/` and per-attempt scratch still need manual cleanup.

If a GCS process crashes while holding `raster/v1/.references.lock`, inspect active
jobs and logs before explicitly removing that lock. It never expires itself into
a possible split-brain publisher. Interrupted runs remain attempt records and
unreferenced artifacts; rerun with the same semantic inputs to recover. Local
per-attempt work folders are disposable after jobs finish and currently require
manual cleanup; object-store GC does not remove local scratch folders.

## Scheduling and validation status

`infra/` prepares a private bucket, separate scheduler/worker identities, a
4-vCPU/8-GiB Cloud Run job every 30 minutes, reference-safe retention, failure
metrics and a 12-hour stale-new-run alert. Cloud `cloud_job` uses the current v1
adapter, restores small source observation metadata (not raw files), freezes a real cutoff and emits the
eligible-run heartbeat only for a new actual source initialization. Config and
geometry URIs must be in a bucket the worker can read. Supply a runtime config
whose risk-config path is valid in the container (or null for the default).
Build a deployment image with `docker buildx build --platform linux/amd64`
for Cloud Run; the locally verified image uses this machine's ARM64 platform.
No resources are deployed by this implementation. Configure notification channel
IDs and enable the required GCP APIs before deployment. Terraform init/validate
and formatting checks have been run; permissions/alert delivery need a deployed
smoke check. Source polling is not a new forecast every 30 minutes.

See [validation/STATUS.md](validation/STATUS.md) for measured results and open
acceptance checks. The single-interval live default-domain check is not the full
72-hour performance acceptance benchmark. Production static files/checksums,
source-data spot checks and the complete live batch benchmark are still required.

## Primary source references

* [DWD versioned ICON directory](https://opendata.dwd.de/weather/nwp/v1/m/icon/p/)
* [DWD ICON output-field documentation](https://www.dwd.de/DWD/forschung/nwv/fepub/icon_database_main.pdf)
* [USDA Tree Canopy Cover catalog](https://data.fs.usda.gov/geodata/rastergateway/treecanopycover/)
* [MRLC NLCD canopy catalog](https://www.mrlc.gov/data/type/nlcd-tree-canopy-cover)
* [CEC transmission inventory and attribute dictionary](https://data.ca.gov/@california-energy-commission/california-electric-transmission-lines)
* [GDAL COG format](https://gdal.org/en/stable/drivers/raster/cog.html)

## Supervised three-window training

The separate `outage_model/` package implements downstream dataset assembly,
purged chronological splits, seasonal/logistic baselines, three native CPU
LightGBM classifiers, global/regularized regional calibration, support screening,
complete additive explanations and independent `outage/v1/` publication. Its
weather-score fallback is explicitly separate from probability. No production
model or deployment is included; real aligned labels/history and untouched
validation are required, with executable readiness failures when absent.

Use `requirements-model.lock` and `Dockerfile.model` for the independent job.
Start with `python -m outage_model.cli --store output readiness`. See
[outage model operations](outage_model/docs/OPERATIONS.md) for complete commands,
input contracts, 5 GB preferred / 10 GB maximum local storage behavior and
remote archival strategy; [data sources](outage_model/docs/DATA_SOURCES.md) for
verified access and outstanding audits; and [model readiness](validation/MODEL_STATUS.md)
for what has actually run. Generated data is confined to tests.
