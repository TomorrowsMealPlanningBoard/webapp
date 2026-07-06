terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }

  # NOTE: バックエンドは意図的に指定していない（ローカル state）。
  # 本番運用でチームで共有する場合は GCS バックエンドの追加を検討すること。
  # 例:
  # backend "gcs" {
  #   bucket = "tomorrows-meal-tfstate"
  #   prefix = "alloydb"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}
