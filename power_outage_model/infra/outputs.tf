output "bucket" { value = "gs://${google_storage_bucket.rasters.name}" }
output "job" { value = google_cloud_run_v2_job.batch.name }
output "worker" { value = google_service_account.worker.email }
output "map_assets_url" {
  value = var.map_bucket_name == null ? null : "https://storage.googleapis.com/${var.map_bucket_name}"
}
