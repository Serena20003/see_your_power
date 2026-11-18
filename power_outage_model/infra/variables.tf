variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-west1"
}
variable "bucket_name" { type = string }
variable "image" {
  type        = string
  description = "Immutable linux/amd64 image digest built from power_outage_model/Dockerfile"
}
variable "config_uri" {
  type        = string
  description = "gs:// URI of the reviewed runtime config; includes pinned static-manifest key and geometry checksum"
}
variable "native_grid_uri" {
  type        = string
  description = "gs:// URI of the pinned ICON geometry"
}
variable "alert_notification_channels" {
  type    = list(string)
  default = []
}
