# Cloud Run デプロイ手順

SPEC.md §4 ループBの「4. レビュー & デプロイ」における自動デプロイの設定手順。

## 概要

`main` ブランチへのマージをトリガーに `.github/workflows/deploy.yml` が自動実行される。  
PR レビュー・承認を経てマージされた後にのみ実行されるため、**Human-in-the-loop** の設計と一致している。

---

## 前提条件

| リソース | 説明 |
|---|---|
| Google Cloud Project | Cloud Run / Artifact Registry が有効なプロジェクト |
| Artifact Registry リポジトリ | `infra/terraform/` の `terraform apply` で作成 |
| Cloud Run サービス | `infra/terraform/` の `terraform apply` で作成（`cloud_run.tf`） |
| Workload Identity Federation | `infra/terraform/` の `terraform apply` で作成（`workload_identity.tf`） |

---

## 初期セットアップ

### 1. Terraform で GCP リソースを一括作成する

WIF・SA・Artifact Registry・Cloud Run サービス・必要な GCP API はすべて `infra/terraform/` で管理されている。  
初回セットアップは `terraform apply` で完結する。

```bash
cd infra/terraform

# terraform.tfvars を作成（テンプレートをコピーして値を埋める）
cp terraform.tfvars.example terraform.tfvars
# ── terraform.tfvars を編集 ──
# project_id  = "your-gcp-project-id"
# github_repo = "TomorrowsMealPlanningBoard/webapp"

terraform init
terraform plan
terraform apply
```

`terraform apply` 後、出力値（`terraform output`）を GitHub Secrets に設定する。

### 2. GitHub Secrets の設定

GitHub リポジトリの **Settings > Secrets and variables > Actions** で以下を設定する：

| Secret 名 | 取得元 | 必須 |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | GCP プロジェクト ID（手動入力） | ✅ |
| `WIF_PROVIDER` | `terraform output wif_provider` | ✅ |
| `WIF_SERVICE_ACCOUNT` | `terraform output wif_service_account` | ✅ |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL（デプロイ失敗通知） | 任意 |

---

## デプロイフロー

```
feature ブランチ
  └─► PR 作成
        └─► CI ワークフロー実行（unit test / docker build）
              └─► レビュー・承認（Human-in-the-loop）
                    └─► main へマージ
                          └─► deploy.yml トリガー
                                ├─► Docker ビルド
                                ├─► Artifact Registry へ push（:sha タグ + :latest タグ）
                                ├─► Cloud Run へイメージ更新（SA・環境変数は Terraform 管理のため変更なし）
                                ├─► ヘルスチェック（/health を最大5回）
                                └─► 失敗時 Slack 通知（SLACK_WEBHOOK_URL 設定時）
```

> **注意**: `deploy.yml` はイメージの更新のみを行う。SA（実行サービスアカウント）・環境変数・IAM は
> `infra/terraform/cloud_run.tf` で宣言的に管理されるため、`deploy.yml` には記載しない。

---

## ヘルスチェック

デプロイ後、`/health` エンドポイントに対して最大5回（10秒間隔）HTTP GET を行う。  
5回すべて HTTP 200 以外の場合はワークフローが失敗し、Slack に通知が送られる。

---

## ロールバック

```bash
# 直前のリビジョンにトラフィックを戻す（手動）
gcloud run services update-traffic tomorrows-meal-webapp \
  --to-revisions=<revision-name>=100 \
  --region=asia-northeast1
```

過去のリビジョン一覧：

```bash
gcloud run revisions list \
  --service=tomorrows-meal-webapp \
  --region=asia-northeast1 \
  --format="table(metadata.name,status.conditions[0].status,spec.containerConcurrency)"
```
