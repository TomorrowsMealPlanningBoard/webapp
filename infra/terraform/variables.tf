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
