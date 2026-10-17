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

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /v1/forecasts/{county_fips}` | Unplanned-outage forecast for the next 24 h / 24–48 h / 48–72 h |
| `GET /v1/planned-outages/{county_fips}` | Source-backed planned outage and PSPS notices |
| `GET /v1/coverage` | County coverage metadata |
| `GET /healthz` | Health check |

## Release gates

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
