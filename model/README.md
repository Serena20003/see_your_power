# Runway Model Service

Dockerized Python forecast pipeline and FastAPI read service for the Runway unplanned-outage predictor.

## Architecture

```
model/
  pipeline/       # Hourly data ingestion + feature engineering
  training/       # Model training, calibration, evaluation
  inference/      # Batch forecast generation
  service/        # FastAPI read service (GET /v1/forecasts/{county_fips})
  tests/          # Unit + integration tests
  Dockerfile      # For Cloud Run jobs (pipeline + inference)
  Dockerfile.api  # For Cloud Run service (FastAPI)
  docker-compose.yml
```

## Development setup

```bash
cd model
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Start local API (reads from fixtures/):
uvicorn service.main:app --reload --port 8080
# Run tests:
pytest
```

For the plan assistant, set `WATSONX_API_KEY` and `WATSONX_PROJECT_ID` in the
repository root `.env.development.local` (or `.env.local`). The service loads
these files at startup, so no shell exports are needed. Existing process
variables take precedence, then `.env.local`, then `.env.development.local`.
Restart the API after changing credentials. The frontend uses the same-origin
API by default (`VITE_API_BASE_URL=/` is also supported). Restart Vite after
changing its settings. Vite forwards
`/v1` and `/health` to `http://127.0.0.1:8080`; use `RUNWAY_DEV_API_URL` to
change that backend target. Pointing the browser directly at port 8080 bypasses
the proxy and fails cross-origin checks because the API is same-origin only.
A response marked `source: "fallback"` means the plan service responded but watsonx was unavailable. Check the API log for the cause.
Copy the project ID and API base URL from your watsonx project's **Developer
access** page. Set `WATSONX_REGION` to the region prefix in that URL (for
example, `eu-de` for `https://eu-de.ml.cloud.ibm.com`). If IBM denies access,
confirm the API key belongs to a user or service ID with access to the project.
The default model is `ibm/granite-4-h-small`; set `WATSONX_MODEL_ID` only to
select another model listed as available in your IBM region.

## API endpoints

### Existing experimental impact model

The default forecast endpoint now serves the fitted `4e97b613` experimental
customer-impact model. `estimated_customer_impact` is separate from `probability`,
which remains null. The model aggregates simulated within-county point allocations
into typical county customer impact using geography and calendar features. It was
fitted to real 2018 county observations with simulated point locations; weather
and canopy were not trained predictors. Severe totals are strongly underestimated.
The three windows are fixed UTC days, with their actual boundaries in the response.

There are 2,758 supported CONUS counties. The 351 supplied geometries outside that
domain receive unavailable, with null estimates; Alaska, Hawaii and territories
are outside this model. Missing forecasts never become zero or synthetic fallback.

`GET /v1/experimental-impact` provides the map's full county export; `/v1/coverage`
uses the same supported domain. The web service reads the public derived file
`https://storage.googleapis.com/runway-515115-map-tiles/experimental-impact/latest.json`
and caches it for five minutes. A refresh failure retains the last good forecast
with its original dates and a stale marker. It never fits a model at request time.

The `Publish experimental county impact` workflow runs daily at 00:05 UTC and on
manual dispatch. It verifies the pinned portable model bundle and publishes a new
three-day export. Its collector service account has write access only to the
`experimental-impact/` prefix in the public map bucket. The web container needs
no additional ML or geospatial dependencies.

To generate a local export with the existing inference environment:

```sh
power_outage_model/.venv312/bin/python power_outage_model/scripts/publish_experimental_impact.py
```

Set `EXPERIMENTAL_IMPACT_PATH` to a local export to avoid GCS reads, or
`EXPERIMENTAL_IMPACT_URL` to override the derived asset URL. Set
`EXPERIMENTAL_IMPACT_ENABLED=false` only to exercise the legacy fixture/unavailable
paths. The browser calls the same-origin API by default; `VITE_OFFLINE_MODE=true`
explicitly selects the old offline demonstration.

| Endpoint | Description |
|---|---|
| `GET /v1/forecasts/{county_fips}` | Experimental customer impact for three fixed UTC days; outage probabilities remain null |
| `GET /v1/planned-outages/{county_fips}` | Source-backed planned outage and PSPS notices |
| `GET /v1/coverage` | County coverage metadata |
| `GET /health` | Health check |

## Release gates for future outage probabilities

Numeric forecasts are gated on:
1. Verified typed (unplanned) training data for the county
2. Usable restoration-time labels
3. Complete nationwide forecast features (weather, terrain, canopy)
4. Demonstrated regional transfer performance (held-out utilities + states)

Counties failing any gate receive `status: "insufficient_data"` in the response.

## Deployment (Google Cloud)

- **Pipeline job**: Cloud Run job triggered by Cloud Scheduler every hour
- **Inference job**: Cloud Run job triggered after pipeline completes
- **API service**: Cloud Run service (always-on, min-instances: 1)
- **Storage**: Cloud Storage (versioned raw data + model artifacts), PostgreSQL (current forecasts)
- **Secrets**: Secret Manager
- **Monitoring**: Cloud Monitoring + alerting on stale batch age

See `infra/` for Terraform configuration.
