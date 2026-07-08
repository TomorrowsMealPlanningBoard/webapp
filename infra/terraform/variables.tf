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

variable "gemini_text_model" {
  description = "テキスト生成用 Gemini モデル名（recipe_generator / source_extractor）。"
  type        = string
  default     = "gemini-3.1-flash-lite"
}

variable "gemini_text_location" {
  description = "テキスト生成用ロケーション。global はグローバルエンドポイントを使用。"
  type        = string
  default     = "global"
}

variable "gemini_vision_model" {
  description = "画像解析用 Gemini モデル名（vision_analyzer）。"
  type        = string
  default     = "gemini-3.1-flash-lite"
}

variable "gemini_vision_location" {
  description = "画像解析用ロケーション。"
  type        = string
  default     = "global"
}

variable "gemini_live_model" {
  description = "Gemini Live API 用モデル名（voice_session）。Vertex AI 経由では gemini-live-2.5-flash-native-audio を使用する（gemini-3.1-flash-live-preview は AI Studio 専用）。"
  type        = string
  default     = "gemini-live-2.5-flash-native-audio"
}

variable "gemini_live_location" {
  description = "Gemini Live API 用ロケーション。gemini-live-2.5-flash-native-audio は global 未対応のため us-central1 固定。"
  type        = string
  default     = "us-central1"
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
