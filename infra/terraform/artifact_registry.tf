# Docker イメージの push/pull 先（deploy.yml の REPOSITORY 変数と一致させる）
resource "google_artifact_registry_repository" "webapp" {
  location      = var.region
  repository_id = var.name_prefix
  format        = "DOCKER"
  project       = var.project_id
}
