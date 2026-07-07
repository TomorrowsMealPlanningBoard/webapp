# Cloud Run 実行サービスアカウント
resource "google_service_account" "cloud_run" {
  account_id   = "${var.name_prefix}-webapp"
  display_name = "TomorrowsMeal Cloud Run 実行SA"
  project      = var.project_id
}

# Firestore（層1/層2/層3'）
resource "google_project_iam_member" "cloud_run_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

# Memory Bank / Vertex AI Agent Engine（層3）
resource "google_project_iam_member" "cloud_run_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}

# Cloud Trace（可観測性）
resource "google_project_iam_member" "cloud_run_trace" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.cloud_run.email}"
}
