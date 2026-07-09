# infra/terraform

TomorrowsMeal の GCP インフラを Terraform で管理する（Issue #86）。

## 管理リソース

| ファイル | リソース |
|---|---|
| `service_account.tf` | Cloud Run 実行 SA + IAM ロール（Firestore / Memory Bank / Cloud Trace） |
| `terraform_deployer.tf` | Terraform CI/CD 用 SA + IAM ロール（WIF バインド・GCS state・editor 相当） |
| `artifact_registry.tf` | Docker イメージリポジトリ（`tomorrows-meal`） |
| `workload_identity.tf` | GitHub Actions 用 Workload Identity Federation |
| `firestore.tf` | Firestore Native モード DB |

## CI/CD パイプライン構成

`infra/terraform/**` を含む変更は以下のワークフローが自動実行する。

```
PR 作成
  └─ terraform-plan.yml  → plan 結果を PR コメントに投稿（レビュー可視化）

PR マージ（main push）
  └─ terraform-apply.yml → terraform apply を自動実行
```

### GitHub Actions シークレット / 変数の設定

| 名前 | 種別 | 値 |
|------|------|----|
| `WIF_PROVIDER` | Secret | `terraform output wif_provider` の値（cloud_run SA 用と共用） |
| `WIF_TF_SERVICE_ACCOUNT` | Secret | `terraform output terraform_deployer_service_account` の値 |
| `GOOGLE_CLOUD_PROJECT` | Secret | GCP プロジェクト ID（例: `agentic-ai-495701`） |
| `GITHUB_REPO` | Variable | `TomorrowsMealPlanningBoard/webapp`（デフォルト値あり、省略可） |

> `WIF_SERVICE_ACCOUNT`（Cloud Run deploy 用）と `WIF_TF_SERVICE_ACCOUNT`（Terraform 用）は
> 別々の SA を指す。最小権限の原則によりインフラ変更権限とアプリ実行権限を分離している。

### env 変数変更時の順序保証

Cloud Run の環境変数は Terraform が管理する（`cloud_run.tf`）。
「新しい env が必須なコード」を同時リリースする場合は以下の順序で行うこと：

1. Terraform 変更 PR をマージ → `terraform-apply.yml` の完了を確認
2. アプリコード PR をマージ → `deploy.yml` が新イメージをデプロイ

通常の機能開発（env 変更なし）では両ワークフローは独立して動作するため順序は不要。

## 初回セットアップ（新規環境構築時）

### 1. GCS state バケットの作成（bootstrap）

state バケット自身は Terraform で管理しない（chicken-and-egg 問題を避けるため）。
一度だけ手動で作成する：

```bash
gcloud storage buckets create gs://tomorrows-meal-tfstate \
  --location=asia-northeast1 \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update gs://tomorrows-meal-tfstate --versioning
```

### 2. Terraform 初回 apply（ローカルから）

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# terraform.tfvars に project_id と github_repo を記入

terraform init
terraform plan   # 内容を確認
terraform apply
```

### 3. GitHub Actions シークレットを設定

```bash
terraform output wif_provider                      # → WIF_PROVIDER
terraform output wif_service_account               # → WIF_SERVICE_ACCOUNT（deploy.yml 用）
terraform output terraform_deployer_service_account # → WIF_TF_SERVICE_ACCOUNT（terraform 用）
# GOOGLE_CLOUD_PROJECT は project_id をそのまま設定
```

## 注意事項

- `terraform.tfvars` は `.gitignore` 対象（コミットしない）
- Firestore DB には `lifecycle.prevent_destroy = true` を設定済み。`terraform destroy` しても Firestore は削除されない
- Memory Bank（Agent Engine）のプロビジョニングは Issue #82 で別途対応
- GCS state バケット（`tomorrows-meal-tfstate`）はバージョニング有効・公開アクセス防止済み
