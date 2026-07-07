# Issue #28 AC: 「Cloud Run から AlloyDB Auth Proxy 経由で IAM 認証で接続できること」
#
# AlloyDB Auth ProxyがIAM認証で接続するには、接続元プリンシパル（Cloud Run実行SA、
# あるいはローカル開発者のGoogleアカウント）に以下が必要:
#   1. roles/alloydb.client（Auth Proxy経由の接続許可）
#   2. roles/serviceusage.serviceUsageConsumer（Auth ProxyがAPIを呼ぶために必要）
#   3. AlloyDB側でIAM認証ユーザーとして追加（google_alloydb_user リソース）

resource "google_project_iam_member" "cloud_run_alloydb_client" {
  project = var.project_id
  role    = "roles/alloydb.client"
  member  = "serviceAccount:${var.cloud_run_service_account_email}"
}

resource "google_project_iam_member" "cloud_run_service_usage_consumer" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:${var.cloud_run_service_account_email}"
}

# --- AlloyDB側のIAM認証データベースユーザー登録 ---
# Cloud Run実行SAをAlloyDBのIAM認証DBユーザーとして登録する。
# ユーザー名はサービスアカウントのメールアドレスそのもの（Auth Proxyの仕様）。
resource "google_alloydb_user" "cloud_run_iam_user" {
  cluster        = google_alloydb_cluster.main.name
  user_id        = var.cloud_run_service_account_email
  user_type      = "ALLOYDB_IAM_USER"
  database_roles = ["alloydbiamuser"]
}

# --- ローカル開発者・追加運用者向けのIAM認証DBユーザー登録 ---
# variables.tf の iam_db_users（Googleアカウント等）を同様にIAM認証ユーザーとして登録する。
resource "google_alloydb_user" "additional_iam_users" {
  for_each = toset(var.iam_db_users)

  cluster        = google_alloydb_cluster.main.name
  user_id        = each.value
  user_type      = "ALLOYDB_IAM_USER"
  database_roles = ["alloydbiamuser"]
}

# --- 追加運用者向けの roles/alloydb.client 付与 ---
resource "google_project_iam_member" "additional_users_alloydb_client" {
  for_each = toset(var.iam_db_users)

  project = var.project_id
  role    = "roles/alloydb.client"
  member  = "user:${each.value}"
}
