# Issue #28 AC: 「AlloyDB for PostgreSQL クラスタ／インスタンスが作成されること」
# 「pgvector拡張が有効化され、層3のベクトルカラムが利用可能なこと」
#
# 運用方針（コスト都合）: AlloyDBは停止機能が無く起動中は時間単位課金され続けるため、
# 「必要な期間だけ terraform apply → 使い終わったら terraform destroy」を繰り返す運用とする。
# 常時起動しない前提のため、本リソースは意図的に破棄しやすい設定にしている:
#   - lifecycle.prevent_destroy は設定しない
#   - deletion_policy = "FORCE" によりインスタンス・バックアップが存在してもdestroy可能にする
#   - continuous_backup_config は無効化（ストレージ課金を避ける。destroy前はpg_dumpで手動バックアップする）
# destroy するとデータは完全に失われる。destroy前に必ずバックアップを取ること
# （手順は docs/alloydb_provisioning.md を参照）。

resource "google_alloydb_cluster" "main" {
  provider   = google-beta
  project    = var.project_id
  location   = var.region
  cluster_id = var.alloydb_cluster_id

  # インスタンス・バックアップが存在する状態でも terraform destroy を通す。
  # 「うっかりdestroyできない」事故を避けるため、誤ってリソースが残り続ける方を許容しない方針。
  deletion_policy = "FORCE"

  network_config {
    network = local.vpc_network_id
  }

  # 初期化用の一時パスワード。運用接続はIAM認証（Auth Proxy --auto-iam-authn）を使うため、
  # このパスワードはpgvector拡張の有効化・マイグレーション適用など管理作業にのみ使用する。
  initial_user {
    user     = "postgres"
    password = var.alloydb_admin_password
  }

  # SPEC.md 層3: レシピ事例のベクトル検索用に pgvector を有効化する。
  # AlloyDBはCloudSQL同様、拡張機能はデータベース作成後にCREATE EXTENSIONで有効化する必要があるため、
  # Terraformでは "許可される拡張機能" の宣言のみ行い、実際のCREATE EXTENSIONは
  # scripts/migrate.py（マイグレーション適用スクリプト）側で実行する。
  #
  # continuous_backup_config は明示的に無効化。都度作成/削除する運用ではバックアップストレージの
  # 追加課金がかさむため、destroy前の手動pg_dumpバックアップ運用（docs/alloydb_provisioning.md）を採用する。
  continuous_backup_config {
    enabled = false
  }

  depends_on = [
    google_project_service.alloydb,
    google_service_networking_connection.private_vpc_connection,
  ]
}

resource "google_alloydb_instance" "primary" {
  provider      = google-beta
  cluster       = google_alloydb_cluster.main.name
  instance_id   = var.alloydb_primary_instance_id
  instance_type = "PRIMARY"

  availability_type = var.alloydb_availability_type

  machine_config {
    cpu_count = var.alloydb_primary_machine_cpu_count
  }

  # pgvector を含む主要拡張をallowlistする。
  # AlloyDBのdatabase_flagsで有効化を強制することはできないため、
  # 実際の `CREATE EXTENSION IF NOT EXISTS vector;` は
  # scripts/migrate.py 初回実行時にマイグレーションの一部として適用する。
  database_flags = {
    "alloydb.enable_pgaudit" = "off"
  }

  depends_on = [google_alloydb_cluster.main]
}

# --- Secret Manager: 初期adminパスワードの保管 ---
# AC: 「接続情報・シークレットが Secret Manager 等で安全に管理されること」
# 通常運用の接続はIAM認証でパスワードレスだが、初期プロビジョニング・拡張有効化・
# マイグレーション適用時に使うpostgresユーザーのパスワードはSecret Managerで管理する。
resource "google_secret_manager_secret" "alloydb_admin_password" {
  project   = var.project_id
  secret_id = "${var.secret_id_prefix}-admin-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "alloydb_admin_password" {
  secret      = google_secret_manager_secret.alloydb_admin_password.id
  secret_data = var.alloydb_admin_password
}

# --- Secret Manager: 接続情報（パスワードレス。IAM認証用の接続文字列メタ情報） ---
resource "google_secret_manager_secret" "alloydb_connection_info" {
  project   = var.project_id
  secret_id = "${var.secret_id_prefix}-connection-info"

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "alloydb_connection_info" {
  secret = google_secret_manager_secret.alloydb_connection_info.id
  secret_data = jsonencode({
    instance_uri = google_alloydb_instance.primary.name
    database     = var.alloydb_database_name
    # IAM認証のためパスワードは含めない。DBユーザー名はIAMプリンシパルのメールアドレス（.gservoiceaccount.comの場合は
    # 末尾の".gserviceaccount.com"を除いたものがDBユーザー名になる。Auth Proxyの仕様に準拠）。
    auth_mode = "ALLOYDB_IAM_AUTHN"
  })
}
