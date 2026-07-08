variable "project_id" {
  description = "GCPプロジェクトID（例: agentic-ai-495701）"
  type        = string
}

variable "region" {
  description = "Cloud Run・Artifact Registry のリージョン。deploy.yml と揃える。"
  type        = string
  default     = "asia-northeast1"
}

variable "name_prefix" {
  description = "作成するリソース名の接頭辞。"
  type        = string
  default     = "tomorrows-meal"
}

variable "github_repo" {
  description = <<-EOT
    Workload Identity Federation で認証を許可する GitHub リポジトリ（owner/repo 形式）。
    例: "TomorrowsMealPlanningBoard/webapp"
  EOT
  type        = string
}

# Cloud Run サービス設定
variable "use_firestore" {
  description = "Firestore を使用するかどうか（true/false）。"
  type        = string
  default     = "true"
}

variable "use_memory_bank" {
  description = "Vertex AI Agent Engine Memory Bank を使用するかどうか（true/false）。"
  type        = string
  default     = "false"
}

variable "memory_bank_agent_engine_id" {
  description = "Vertex AI Agent Engine ID（USE_MEMORY_BANK=true のときに使用）。"
  type        = string
  default     = ""
}

variable "gemini_model" {
  description = "通常の Gemini モデル名。"
  type        = string
  default     = "gemini-3.1-flash-lite"
}

variable "gemini_live_model" {
  description = "Gemini Live API 用モデル名。"
  type        = string
  default     = "gemini-3.1-flash-live-preview"
}

variable "gemini_location" {
  description = "Gemini API のロケーション。global はグローバルエンドポイントを使用（asia-northeast1 ではモデルが見つからないことがある）。"
  type        = string
  default     = "global"
}

variable "google_client_id" {
  description = "Google OAuth クライアント ID（Issue #90 の OAuth 対応で使用）。"
  type        = string
  default     = ""
}

variable "cloud_run_min_instances" {
  description = "Cloud Run の最小インスタンス数。"
  type        = number
  default     = 0
}

variable "cloud_run_max_instances" {
  description = "Cloud Run の最大インスタンス数。"
  type        = number
  default     = 1
}

variable "cloud_run_memory" {
  description = "Cloud Run コンテナのメモリ上限。"
  type        = string
  default     = "1Gi"
}

variable "cloud_run_cpu" {
  description = "Cloud Run コンテナの CPU 上限。"
  type        = string
  default     = "1"
}
