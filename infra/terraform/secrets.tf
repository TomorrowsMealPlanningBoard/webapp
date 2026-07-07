# JWT署名鍵を Secret Manager で管理する。
# Cloud Run 実行SA に secretAccessor を付与し、cloud_run.tf で secret_key_ref として参照する。

variable "jwt_secret_key" {
  description = "JWT署名鍵の初期値。terraform apply 時に -var='jwt_secret_key=...' で渡す。未指定の場合は Secret Manager コンソールで手動設定する。"
  type        = string
  sensitive   = true
  default     = ""
}

resource "google_secret_manager_secret" "jwt_secret_key" {
  project   = var.project_id
  secret_id = "jwt-secret-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "jwt_secret_key" {
  count = var.jwt_secret_key != "" ? 1 : 0

  secret      = google_secret_manager_secret.jwt_secret_key.id
  secret_data = var.jwt_secret_key
}

# Cloud Run 実行SA に Secret Manager の読み取り権限を付与する
resource "google_secret_manager_secret_iam_member" "cloud_run_jwt_secret" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.jwt_secret_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.cloud_run.email}"
}
