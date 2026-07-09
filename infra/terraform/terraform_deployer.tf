# Terraform CI/CD 用サービスアカウント（GitHub Actions から terraform plan/apply を実行する）
#
# cloud_run SA（アプリ実行権限）とは意図的に分離している。
# インフラ変更権限とアプリ実行権限を同一 SA に混在させると最小権限の原則に反するため。

resource "google_service_account" "terraform_deployer" {
  account_id   = "${var.name_prefix}-tf-deployer"
  display_name = "TomorrowsMeal Terraform Deployer SA"
  project      = var.project_id
}

# WIF: terraform-plan.yml / terraform-apply.yml が impersonate できるようにする
resource "google_service_account_iam_member" "tf_deployer_wif" {
  service_account_id = google_service_account.terraform_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# GCS state バケットへの読み書き（terraform init/plan/apply に必要）
resource "google_storage_bucket_iam_member" "tf_deployer_state" {
  bucket = "tomorrows-meal-tfstate"
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.terraform_deployer.email}"
}

# プロジェクト全体の editor 相当（Terraform が管理するリソース群を作成・更新するため）
# editor は IAM 設定変更権限を持たないため、IAM 系リソースには下記の追加ロールが必要
resource "google_project_iam_member" "tf_deployer_editor" {
  project = var.project_id
  role    = "roles/editor"
  member  = "serviceAccount:${google_service_account.terraform_deployer.email}"
}

# IAM member 付与・変更に必要（service_account.tf / workload_identity.tf で使用）
resource "google_project_iam_member" "tf_deployer_iam_admin" {
  project = var.project_id
  role    = "roles/resourcemanager.projectIamAdmin"
  member  = "serviceAccount:${google_service_account.terraform_deployer.email}"
}

# Service Account の作成・更新（service_account.tf で google_service_account を管理するため）
resource "google_project_iam_member" "tf_deployer_sa_admin" {
  project = var.project_id
  role    = "roles/iam.serviceAccountAdmin"
  member  = "serviceAccount:${google_service_account.terraform_deployer.email}"
}
