# Outage model — operational setup

This document covers the one-time GCS + GitHub Actions setup required to start
continuous Cal OES collection.  Software implementation is complete; only
cloud credentials and a bucket are needed.

---

## 1. Create a GCS bucket

```bash
PROJECT=your-gcp-project-id
BUCKET=see-your-power-outage   # must be globally unique

gcloud storage buckets create gs://$BUCKET \
  --project=$PROJECT \
  --location=US \
  --uniform-bucket-level-access
```

Do **not** enable object versioning (the pipeline uses generation-based CAS).

Apply a lifecycle rule to cap raw-request log storage (optional):

```bash
cat > /tmp/lifecycle.json <<'EOF'
{
  "rule": [{
    "action": {"type": "Delete"},
    "condition": {
      "matchesPrefix": ["outage/v1/sources/california/requests/"],
      "age": 90
    }
  }]
}
EOF
gcloud storage buckets update gs://$BUCKET --lifecycle-file=/tmp/lifecycle.json
```

Never apply lifecycle rules to `outage/v1/sources/california/snapshots/` or
`outage/v1/sources/california/events/` — those are the durable label archive.

---

## 2. Create a service account

```bash
SA=outage-collector
gcloud iam service-accounts create $SA \
  --project=$PROJECT \
  --display-name="Outage model collector"

gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member="serviceAccount:$SA@$PROJECT.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# Export the key as JSON
gcloud iam service-accounts keys create /tmp/sa-key.json \
  --iam-account=$SA@$PROJECT.iam.gserviceaccount.com
```

---

## 3. Add GitHub repository secrets and variables

Go to **Settings → Secrets and variables → Actions** in your repository.

| Kind | Name | Value |
|---|---|---|
| Secret | `GCP_SA_KEY` | Full contents of `/tmp/sa-key.json` |
| Variable | `CALOES_GCS_BUCKET` | `gs://see-your-power-outage` |

Delete `/tmp/sa-key.json` from your machine after adding it.

---

## 4. Enable the workflow

The workflow file is at `.github/workflows/collect-caloes.yml`.  It runs
automatically on merge to any branch once GitHub Actions is enabled for the
repository.  You can also trigger it manually from the **Actions** tab using
the **Run workflow** button to verify credentials before the first scheduled
run.

GitHub Actions free tier provides 2,000 minutes/month.  Each collect run takes
roughly 30 seconds, so 15-minute polling uses ~48 min/day (~1,440 min/month),
well within the free tier.

---

## 5. Verify a snapshot was written

After the first successful run, list the most recent snapshot keys:

```bash
gcloud storage ls gs://$BUCKET/outage/v1/sources/california/snapshots/ \
  | tail -5
```

Each key is a content-addressed SHA256 hash.  Inspect one:

```bash
gcloud storage cat gs://$BUCKET/outage/v1/sources/california/snapshots/<key>.json \
  | python3 -m json.tool | head -40
```

---

## 6. What is stored

| Path prefix | Contents | Retention |
|---|---|---|
| `outage/v1/sources/california/raw/` | Full ArcGIS API response payloads | 90 days (lifecycle) |
| `outage/v1/sources/california/requests/` | Per-request logs | 90 days (lifecycle) |
| `outage/v1/sources/california/snapshots/` | `RawSnapshot` metadata records | **Permanent** |
| `outage/v1/sources/california/events/` | Normalized `OutageEvent` lists | **Permanent** |

Snapshots and events are immutable and content-addressed.  A second write of
identical data is a no-op.

---

## 7. Source policy (required for training labels)

The workflow deliberately omits `--source-policy`.  Without it:

- Raw snapshots and normalized events are archived correctly.
- All events are stored with `outage_type='unknown'`.
- No events will qualify as training labels (`reconcile_events` will accept
  them into the ledger, but `build_labels` requires `type_evidence` and
  `location_quality='audited'` before emitting a non-null label).

Once you have audited Cal OES's type mappings, onset reliability, and geometry
semantics for each utility, create a `configs/caloes-policy-v1.json` and pass
it via `--source-policy configs/caloes-policy-v1.json` in the workflow.  See
`IMPLEMENTATION_PLAN.md` §3 (California first) for the required fields.

---

## 8. Running locally

```bash
cd power_outage_model
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa-key.json

python -m outage_model.cli \
  --store gs://see-your-power-outage \
  collect
```

Use `--test-mode` with a local `--store /tmp/outage-test` for offline
development; fixture and live data are strictly separated.
