terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # NOTE: バックエンドは意図的に指定していない（ローカル state）。
  # チームで共有する場合は GCS バックエンドの追加を検討すること。
  # backend "gcs" {
  #   bucket = "tomorrows-meal-tfstate"
  #   prefix = "terraform/state"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
