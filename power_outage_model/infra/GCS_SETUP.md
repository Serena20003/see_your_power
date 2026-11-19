# Raster map deployment

Project: `runway-515115`, region: `us-west1`.

- Private science/config/geometry: `gs://runway-weather`.
- Public derived map assets only: `gs://runway-515115-map-tiles`.
- Worker: `raster-batch-worker@runway-515115.iam.gserviceaccount.com` has object access to both buckets.
- Scheduler: `raster-batch-scheduler@runway-515115.iam.gserviceaccount.com` invokes the job.
- Schedule: `0 3,9,15,21 * * *`, UTC; four runs after ICON source releases.
- Browser manifests: `/canopy/latest.json`, `/weather/latest.json` at `https://storage.googleapis.com/runway-515115-map-tiles`.

The frontend reads the public map bucket by default. `VITE_MAP_ASSETS_URL` overrides it. Weather refreshes every five minutes and the selected forecast window switches the georeferenced image. Scientific COGs are never made public. Rasters sit beneath the basemap water fill, retaining its shoreline and labels. Display exports contain per-pixel maxima over the three 24-hour windows, not county averages or probabilities. Source issue time and stale status remain visible.

## Prepare static inputs

Use Python 3.12 and `pip install -r requirements-static.txt` (the raster lock plus pyshp for the downloaded shapefiles). `scripts/prepare_static_inputs.py` reads the previously downloaded source archives from `.cache/static-inputs`, writes a land-only CONUS mask from TIGER2026 intersected with GSHHG 2.3.7 full-resolution L1-L2+L3-L4, pins actual canopy Albers CRS, and transforms the CEC export from EPSG:3857 to WGS84. Its `preparation.json` records source hashes and the recipe. GSHHG is LGPL; its attribution/license remains in the private static manifest. No reviewed transmission assessment was supplied, so the assessment footprint is explicitly empty and asset coverage remains unassessed. Weather scores do not depend on transmission exposure.

```
python scripts/prepare_static_inputs.py
python -m raster_pipeline.cli build-static \
  --config .cache/static-prepared/static-live.json --output output
```

`canopy_workers` in the prepared config can enable bounded parallel area aggregation. Source pixels and partial-pixel land overlap areas remain unchanged. Keep the resulting static manifest and all its referenced objects together when uploading to GCS. Runtime config sets `static_manifest` to its object key, `native_grid_sha256` to the verified ICON grid hash, and `risk_config` to `/pipeline/raster_pipeline/configs/environmental-heuristic-v2.json` in the worker image.

## Build canopy map tiles

```
python -m raster_pipeline.canopy_tiles \
  --source .cache/static-inputs/nlcd-tcc-2025-v2025-6/nlcd_tcc_conus_wgs84_v2025-6_20250101_20251231.tif \
  --sha256 4791b50ab235329593fd97fe1b9268d7841bd325b5c505f0344da9397d91b041 \
  --vintage 'NLCD TCC CONUS 2025 v2025-6' \
  --output .cache/map-assets --maxzoom 12 --workers 6
```

Upload immutable tile directories before uploading `canopy/latest.json`. Exclude `.tmp` files when syncing a running generation. Tiles use year-long immutable caching; `latest.json` uses `no-store`. The source is 30 m; wider zoom levels average source values. Missing data is transparent; real zero canopy is retained. Generation resumes existing complete tiles. Never commit generated tiles or raw rasters to Git.

## Live job

Build `power_outage_model/Dockerfile` for linux/amd64 and deploy its immutable image digest to Cloud Run Job `hourly-weather-risk`. Arguments:

```
python -m raster_pipeline.cloud_job \
  --config-uri gs://runway-weather/pipeline-inputs/<pinned-runtime-config>.json \
  --native-grid-uri gs://runway-weather/pipeline-inputs/icon_grid_0026_R03B07_G.nc \
  --bucket runway-weather --map-bucket runway-515115-map-tiles
```

Use 4 CPUs, 8 GiB, one task, and a 30-minute timeout. The job first checks source candidates and current publication, avoiding raw downloads on no-op polls. Candidate names only narrow downloads: decoded GRIB metadata and the full existing pipeline still validate all selected data. Sparse late leads are not interpolated into hourly forecasts. No eligible complete 72-hour hourly source leaves the existing publication untouched. The public weather pointer is advanced only after a validated scientific batch is published. Synthetic fixtures cannot become public weather.

```
gcloud run jobs execute hourly-weather-risk --region us-west1 --project runway-515115 --wait
```

Cloud resources were configured with gcloud. Import the existing resources into a Terraform state before applying `infra/main.tf`; the Terraform definition now includes optional public map storage and the four-times-daily schedule.

## Environmental coverage policy

The default remains strict (`allow_static_gaps=false`). An optional `allow_static_gaps=true` publishes risk only where the pinned static coverage code is supported and at least 95% of land has usable canopy input. Unsupported cells stay NoData, and every published batch/map declares `coverage_policy=supported_land` with exact coverage counts. Completely unsupported domains, missing hourly weather, unexplained risk holes, and scores in unsupported cells are rejected. Recalculation preserves the original coverage policy. The deployed job enables this option following approval on November 18, 2026. Its immutable config is `gs://runway-weather/pipeline-inputs/runtime-6cde48d423f0f273.json`, pinned to static version `38c98ac110874ff7cf1e376a`: 53,881 analysis land cells, 53,777 supported cells, and 104 transparent gaps. The library default stays strict for other deployments.

Cumulative rainfall is differenced with the sum of the two decoded GRIB packing-error bounds (minimum 0.0001 mm), with only negative differences inside that bound set to zero. Larger decreases and negative cumulative fields are rejected. Each interval records the bound; normalization recipe v4 distinguishes these outputs from earlier processing. See the ecCodes FAQ on negative precipitation accumulations.
