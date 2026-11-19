terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "6.50.0"
    }
  }
}
provider "google" {
  project = var.project_id
  region  = var.region
}
resource "google_storage_bucket" "rasters" {
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = false }
  soft_delete_policy { retention_duration_seconds = 0 }
  # Scientific objects are collected by the reference-aware gc command.
  # Unconditional age rules would delete a last-good batch or replay dependency.
  lifecycle_rule {
    action { type = "Delete" }
    condition {
      age            = 2
      matches_prefix = ["scratch/"]
    }
  }

}
resource "google_service_account" "worker" {
  account_id   = "raster-batch-worker"
  display_name = "Weather raster batch worker"
}
resource "google_storage_bucket" "map_assets" {
  count                       = var.map_bucket_name == null ? 0 : 1
  name                        = var.map_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  versioning { enabled = false }
  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Cache-Control", "ETag"]
    max_age_seconds = 3600
  }
}
resource "google_storage_bucket_iam_member" "map_viewers" {
  count  = var.map_bucket_name == null ? 0 : 1
  bucket = google_storage_bucket.map_assets[0].name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
resource "google_storage_bucket_iam_member" "map_writer" {
  count  = var.map_bucket_name == null ? 0 : 1
  bucket = google_storage_bucket.map_assets[0].name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_service_account" "scheduler" {
  account_id = "raster-batch-scheduler"
}
resource "google_storage_bucket_iam_member" "objects" {
  bucket = google_storage_bucket.rasters.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.worker.email}"
}
resource "google_cloud_run_v2_job" "batch" {
  name                = "hourly-weather-risk"
  location            = var.region
  deletion_protection = true
  template {
    parallelism = 1
    task_count  = 1
    template {
      service_account = google_service_account.worker.email
      timeout         = "1800s"
      max_retries     = 1
      containers {
        image   = var.image
        command = ["python", "-m", "raster_pipeline.cloud_job"]
        args    = concat(["--config-uri", var.config_uri, "--native-grid-uri", var.native_grid_uri, "--bucket", google_storage_bucket.rasters.name], var.map_bucket_name == null ? [] : ["--map-bucket", var.map_bucket_name])
        resources {
          limits = { cpu = "4", memory = "8Gi" }
        }
        env {
          name  = "OMP_NUM_THREADS"
          value = "4"
        }
      }
    }
  }
}
resource "google_cloud_run_v2_job_iam_member" "invoke" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.batch.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}
resource "google_cloud_scheduler_job" "poll" {
  name      = "poll-hourly-weather"
  # ICON issues four model runs daily. Allow time for the hourly files to arrive.
  schedule  = "0 3,9,15,21 * * *"
  time_zone = "Etc/UTC"
  region    = var.region
  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.batch.name}:run"
    oauth_token { service_account_email = google_service_account.scheduler.email }
  }
}
resource "google_logging_metric" "failed" {
  name   = "raster_pipeline_failed_attempts"
  filter = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"hourly-weather-risk\" AND (jsonPayload.stage=\"failed\" OR jsonPayload.stage=\"cloud_job_failed\")"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}
resource "google_logging_metric" "eligible" {
  name   = "raster_pipeline_eligible_runs"
  filter = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"hourly-weather-risk\" AND jsonPayload.stage=\"eligible_complete_run\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}
resource "google_monitoring_alert_policy" "stale" {
  display_name          = "Weather pipeline: no new eligible complete run for 12 hours"
  combiner              = "OR"
  notification_channels = var.alert_notification_channels
  conditions {
    display_name = "No eligible-run heartbeat for 12h"
    condition_absent {
      filter   = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.eligible.name}\" AND resource.type=\"cloud_run_job\""
      duration = "43200s"
    }
  }
}
resource "google_monitoring_alert_policy" "failures" {
  display_name          = "Weather pipeline: repeated processing failures"
  combiner              = "OR"
  notification_channels = var.alert_notification_channels
  conditions {
    display_name = "Three failures in 90 minutes"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.failed.name}\" AND resource.type=\"cloud_run_job\""
      comparison      = "COMPARISON_GT"
      threshold_value = 2
      duration        = "0s"
      aggregations {
        alignment_period     = "5400s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }
}
