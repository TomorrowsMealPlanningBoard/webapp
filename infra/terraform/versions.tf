terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # state は GCS バックエンドで管理（ローカル state はチーム/CI共有不可のため廃止）。
  # バケットは bootstrap 用に一度だけ手動作成（chicken-and-egg問題を避けるためTerraform管理外）:
  #   gcloud storage buckets create gs://tomorrows-meal-tfstate --location=asia-northeast1 \
  #     --uniform-bucket-level-access --public-access-prevention
  #   gcloud storage buckets update gs://tomorrows-meal-tfstate --versioning
  backend "gcs" {
    bucket = "tomorrows-meal-tfstate"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
