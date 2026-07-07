# infra/terraform

TomorrowsMeal の GCP インフラを Terraform で管理する（Issue #86）。

## 管理リソース

| ファイル | リソース |
|---|---|
| `service_account.tf` | Cloud Run 実行 SA + IAM ロール（Firestore / Memory Bank / Cloud Trace） |
| `artifact_registry.tf` | Docker イメージリポジトリ（`tomorrows-meal`） |
| `workload_identity.tf` | GitHub Actions 用 Workload Identity Federation |
| `firestore.tf` | Firestore Native モード DB |

AlloyDB(pgvector) 採用時の IaC（Issue #28）は Epic #75 に伴い撤去。歴史記録は
[`../terraform-archive/alloydb-deprecated/`](../terraform-archive/alloydb-deprecated/) を参照。

## 初回セットアップ

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars に project_id と github_repo を記入

terraform init
terraform plan   # 内容を確認
terraform apply
```

apply 後、GitHub Actions シークレットに以下を設定する：

```bash
terraform output wif_provider        # → WIF_PROVIDER シークレットの値
terraform output wif_service_account # → WIF_SERVICE_ACCOUNT シークレットの値
# GOOGLE_CLOUD_PROJECT は project_id をそのまま設定
```

Cloud Run サービスの `--service-account` には `cloud_run_service_account_email` の出力値を使う。

## 注意事項

- `terraform.tfvars` は `.gitignore` 対象（コミットしない）
- Firestore DB には `lifecycle.prevent_destroy = true` を設定済み。`terraform destroy` しても Firestore は削除されない
- Memory Bank（Agent Engine）のプロビジョニングは Issue #82 で別途対応
