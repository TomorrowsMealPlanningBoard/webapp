# terraform test（ユニット）: モックプロバイダを使い GCP に接続せず構造を検証する。
# 実行: terraform test
# CI:  terraform-plan.yml / terraform-apply.yml の前段ステップとして組み込む。

# テスト全体で使う共通変数
variables {
  project_id  = "test-project-12345"
  github_repo = "TestOrg/webapp"
}

# google プロバイダをモック化（GCP に接続しない）
mock_provider "google" {}

# ─────────────────────────────────────────────
# 1. 命名規則テスト
# ─────────────────────────────────────────────
run "naming_uses_name_prefix" {
  command = plan

  assert {
    condition     = google_service_account.cloud_run.account_id == "tomorrows-meal-webapp"
    error_message = "Cloud Run SA の account_id が name_prefix を使っていない: ${google_service_account.cloud_run.account_id}"
  }

  assert {
    condition     = google_service_account.terraform_deployer.account_id == "tomorrows-meal-tf-deployer"
    error_message = "Terraform deployer SA の account_id が name_prefix を使っていない: ${google_service_account.terraform_deployer.account_id}"
  }

  assert {
    condition     = google_artifact_registry_repository.webapp.repository_id == "tomorrows-meal"
    error_message = "Artifact Registry の repository_id が name_prefix を使っていない: ${google_artifact_registry_repository.webapp.repository_id}"
  }
}

# ─────────────────────────────────────────────
# 2. Firestore の設定テスト
# ─────────────────────────────────────────────
run "firestore_is_native_mode" {
  command = plan

  assert {
    condition     = google_firestore_database.default.type == "FIRESTORE_NATIVE"
    error_message = "Firestore のモードが NATIVE でない: ${google_firestore_database.default.type}"
  }

  assert {
    condition     = google_firestore_database.default.name == "(default)"
    error_message = "Firestore DB 名が (default) でない: ${google_firestore_database.default.name}"
  }
}

# ─────────────────────────────────────────────
# 3. Workload Identity Federation テスト
# ─────────────────────────────────────────────
run "wif_restricts_to_github_repo" {
  command = plan

  assert {
    condition     = google_iam_workload_identity_pool_provider.github.attribute_condition == "attribute.repository == \"TestOrg/webapp\""
    error_message = "WIF の attribute_condition が github_repo 変数を参照していない: ${google_iam_workload_identity_pool_provider.github.attribute_condition}"
  }

  assert {
    condition     = google_iam_workload_identity_pool_provider.github.oidc[0].issuer_uri == "https://token.actions.githubusercontent.com"
    error_message = "WIF の issuer_uri が GitHub Actions のものでない: ${google_iam_workload_identity_pool_provider.github.oidc[0].issuer_uri}"
  }
}

# ─────────────────────────────────────────────
# 4. Cloud Run の IAM テスト（公開アクセス設定）
# ─────────────────────────────────────────────
run "cloud_run_is_publicly_accessible" {
  command = plan

  assert {
    condition     = google_cloud_run_v2_service_iam_member.public_invoker.role == "roles/run.invoker"
    error_message = "Cloud Run の public_invoker ロールが正しくない: ${google_cloud_run_v2_service_iam_member.public_invoker.role}"
  }

  assert {
    condition     = google_cloud_run_v2_service_iam_member.public_invoker.member == "allUsers"
    error_message = "Cloud Run の public_invoker メンバーが allUsers でない: ${google_cloud_run_v2_service_iam_member.public_invoker.member}"
  }
}

# ─────────────────────────────────────────────
# 5. Secret Manager の条件分岐テスト
#    jwt_secret_key を渡したときだけ secret version が作られること
# ─────────────────────────────────────────────
run "secret_version_created_when_key_provided" {
  command = plan

  variables {
    jwt_secret_key = "dummy-secret-for-test"
  }

  assert {
    condition     = length(google_secret_manager_secret_version.jwt_secret_key) == 1
    error_message = "jwt_secret_key を渡したのに secret version が作られない"
  }
}

run "secret_version_not_created_when_key_empty" {
  command = plan

  variables {
    jwt_secret_key = ""
  }

  assert {
    condition     = length(google_secret_manager_secret_version.jwt_secret_key) == 0
    error_message = "jwt_secret_key が空なのに secret version が作られる"
  }
}

# ─────────────────────────────────────────────
# 6. Terraform deployer SA のリージョン・プロジェクトテスト
# ─────────────────────────────────────────────
run "deployer_sa_in_correct_project" {
  command = plan

  assert {
    condition     = google_service_account.terraform_deployer.project == "test-project-12345"
    error_message = "Terraform deployer SA のプロジェクトが変数と一致しない: ${google_service_account.terraform_deployer.project}"
  }

  assert {
    condition     = google_storage_bucket_iam_member.tf_deployer_state.role == "roles/storage.objectAdmin"
    error_message = "GCS state バケットの権限が objectAdmin でない: ${google_storage_bucket_iam_member.tf_deployer_state.role}"
  }
}
