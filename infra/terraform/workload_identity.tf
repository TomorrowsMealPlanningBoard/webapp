# GitHub Actions が GCP に認証するための Workload Identity Federation
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${var.name_prefix}-github-2"
  display_name              = "GitHub Actions pool"
  project                   = var.project_id
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions provider"
  project                            = var.project_id

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # 指定リポジトリからのトークンのみ許可
  attribute_condition = "attribute.repository == \"${var.github_repo}\""
}

# GitHub Actions が Cloud Run 実行 SA を impersonate できるようにする
resource "google_service_account_iam_member" "wif_impersonation" {
  service_account_id = google_service_account.cloud_run.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# GitHub Actions が gcloud run deploy 時に Cloud Run 実行 SA を指定できるようにする
resource "google_service_account_iam_member" "wif_act_as" {
  service_account_id = google_service_account.cloud_run.name
  role               = "roles/iam.serviceAccountUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
