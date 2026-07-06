output "alloydb_cluster_name" {
  description = "作成されたAlloyDBクラスタのフルリソース名。"
  value       = google_alloydb_cluster.main.name
}

output "alloydb_cluster_id" {
  description = "AlloyDBクラスタID。"
  value       = google_alloydb_cluster.main.cluster_id
}

output "alloydb_primary_instance_name" {
  description = "プライマリインスタンスのフルリソース名。scripts/db_healthcheck.py の ALLOYDB_INSTANCE_URI に設定する。"
  value       = google_alloydb_instance.primary.name
}

output "alloydb_primary_instance_ip" {
  description = "プライマリインスタンスのプライベートIPアドレス。"
  value       = google_alloydb_instance.primary.ip_address
}

output "alloydb_database_name" {
  description = "アプリケーションが利用する論理データベース名。"
  value       = var.alloydb_database_name
}

output "vpc_network_name" {
  description = "AlloyDBが所属するVPCネットワーク名。"
  value       = local.vpc_network_name
}

output "secret_admin_password_id" {
  description = "初期postgresユーザーパスワードを保管するSecret ManagerのシークレットID。"
  value       = google_secret_manager_secret.alloydb_admin_password.secret_id
}

output "secret_connection_info_id" {
  description = "接続情報（パスワードレス・IAM認証メタデータ）を保管するSecret ManagerのシークレットID。"
  value       = google_secret_manager_secret.alloydb_connection_info.secret_id
}

output "cloud_run_iam_db_user" {
  description = "AlloyDBにIAM認証ユーザーとして登録されたCloud Run実行サービスアカウント。"
  value       = google_alloydb_user.cloud_run_iam_user.user_id
}
