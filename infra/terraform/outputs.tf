output "cloud_run_service_account_email" {
  description = "Cloud Run 実行SAのメールアドレス。Cloud Run サービスの --service-account に指定する。"
  value       = google_service_account.cloud_run.email
}

output "wif_provider" {
  description = "GitHub Actions シークレット WIF_PROVIDER に設定する値。"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "wif_service_account" {
  description = "GitHub Actions シークレット WIF_SERVICE_ACCOUNT に設定する値。"
  value       = google_service_account.cloud_run.email
}

output "artifact_registry_repository" {
  description = "Artifact Registry リポジトリの完全パス。"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.webapp.repository_id}"
}

output "cloud_run_service_url" {
  description = "Cloud Run サービスの URL。"
  value       = google_cloud_run_v2_service.webapp.uri
}

output "cloud_run_service_name" {
  description = "Cloud Run サービス名。deploy.yml の CLOUD_RUN_SERVICE に設定する値。"
  value       = google_cloud_run_v2_service.webapp.name
}

output "terraform_deployer_service_account" {
  description = "Terraform CI/CD 用 SA。terraform-plan.yml / terraform-apply.yml の WIF_TF_SERVICE_ACCOUNT シークレットに設定する。"
  value       = google_service_account.terraform_deployer.email
}
