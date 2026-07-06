# Issue #28: AlloyDB(pgvector) プロビジョニング用変数定義。
# ハードコードを避けるため、プロジェクトID等はすべて変数化する。
# 値は terraform.tfvars（gitignore対象）で指定する。terraform.tfvars.example を参照。

variable "project_id" {
  description = "GCPプロジェクトID（例: agentic-ai-495701）"
  type        = string
}

variable "region" {
  description = "リソースを作成するリージョン。.github/workflows/deploy.yml の Cloud Run と揃える。"
  type        = string
  default     = "asia-northeast1"
}

variable "environment" {
  description = "環境名（dev/staging/prod等）。リソース名のサフィックスに使う。"
  type        = string
  default     = "dev"
}

variable "name_prefix" {
  description = "作成するリソース名の接頭辞。"
  type        = string
  default     = "tomorrows-meal"
}

# --- AlloyDB クラスタ／インスタンス ---

variable "alloydb_cluster_id" {
  description = "AlloyDBクラスタID。"
  type        = string
  default     = "tomorrows-meal-alloydb"
}

variable "alloydb_database_name" {
  description = "アプリケーションが利用する論理データベース名。"
  type        = string
  default     = "tomorrows_meal"
}

variable "alloydb_primary_instance_id" {
  description = "AlloyDBプライマリインスタンスID。"
  type        = string
  default     = "tomorrows-meal-primary"
}

variable "alloydb_primary_machine_cpu_count" {
  description = "プライマリインスタンスのvCPU数。AlloyDBは2から選択可能（最小構成）。"
  type        = number
  default     = 2
}

variable "alloydb_availability_type" {
  description = "REGIONAL（マルチAZ・高可用）またはZONAL（単一AZ・低コスト）。MVP/ハッカソンではZONALを推奨。"
  type        = string
  default     = "ZONAL"

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.alloydb_availability_type)
    error_message = "alloydb_availability_type は ZONAL または REGIONAL のいずれかを指定してください。"
  }
}

variable "alloydb_admin_password" {
  description = <<-EOT
    AlloyDBクラスタ初期化用の一時的なpostgresユーザーパスワード。
    実運用の接続はIAM認証（Auth Proxyの--auto-iam-authn）を使うため、
    このパスワードは初期プロビジョニング・pgvector拡張の有効化・マイグレーション適用時にのみ使用する想定。
    平文でtfvarsに書かず、`terraform apply` 実行時に環境変数 TF_VAR_alloydb_admin_password で渡すか、
    事前にSecret Managerで生成したものを参照すること。
  EOT
  type        = string
  sensitive   = true
  default     = null
}

# --- VPC / プライベートサービスアクセス ---

variable "vpc_network_name" {
  description = "AlloyDB用に作成するVPCネットワーク名。既存VPCを再利用する場合はそちらの名前を指定する。"
  type        = string
  default     = "tomorrows-meal-vpc"
}

variable "create_vpc_network" {
  description = "true の場合、専用VPCネットワークを新規作成する。既存VPCを使う場合はfalseにしvpc_network_nameに既存名を指定。"
  type        = bool
  default     = true
}

variable "private_services_access_range" {
  description = "AlloyDB用プライベートサービスアクセスに割り当てるCIDR範囲のプレフィックス長。"
  type        = number
  default     = 16
}

# --- Cloud Run / IAM 連携 ---

variable "cloud_run_service_account_email" {
  description = <<-EOT
    Cloud Run実行サービスアカウントのメールアドレス。
    .github/workflows/deploy.yml がデプロイするサービス(tomorrows-meal-webapp)に紐づく
    実行時サービスアカウントを指定する（未指定ならデフォルトのCompute Engineサービスアカウント）。
    このSAに roles/alloydb.client と roles/serviceusage.serviceUsageConsumer を付与する。
  EOT
  type        = string
}

variable "iam_db_users" {
  description = <<-EOT
    AlloyDB IAM認証を許可するデータベースユーザー（IAM Auth Proxy経由でログインできるプリンシパル）のリスト。
    Cloud RunのサービスアカウントやローカルユーザーのGoogleアカウントを指定する。
    例: ["cloud-run-sa@PROJECT.iam.gserviceaccount.com", "developer@example.com"]
  EOT
  type        = list(string)
  default     = []
}

# --- Secret Manager ---

variable "secret_id_prefix" {
  description = "Secret Manager に作成するシークレット名の接頭辞。"
  type        = string
  default     = "tomorrows-meal-db"
}
