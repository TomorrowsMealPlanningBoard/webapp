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
| Artifact Registry リポジトリ | `asia-northeast1` の `tomorrows-meal` という名前のリポジトリ |
| Cloud Run サービス | `tomorrows-meal-webapp`（初回は手動作成またはワークフロー初回実行で作成） |
| Workload Identity Federation | GitHub Actions から GCP に認証するための設定 |

---

## 初期セットアップ

### 1. Artifact Registry リポジトリの作成

```bash
gcloud artifacts repositories create tomorrows-meal \
  --repository-format=docker \
  --location=asia-northeast1 \
  --description="TomorrowsMeal webapp images"
```

### 2. Workload Identity Federation の設定

```bash
PROJECT_ID="<your-gcp-project-id>"
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
REPO="TomorrowsMealPlanningBoard/webapp"
POOL_ID="github-actions-pool"
PROVIDER_ID="github-actions-provider"
SA_NAME="github-actions-deployer"

# サービスアカウントの作成
gcloud iam service-accounts create "${SA_NAME}" \
  --display-name="GitHub Actions Deployer" \
  --project="${PROJECT_ID}"

# 必要な権限を付与
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

# Workload Identity Pool の作成
gcloud iam workload-identity-pools create "${POOL_ID}" \
  --location="global" \
  --display-name="GitHub Actions Pool" \
  --project="${PROJECT_ID}"

# Workload Identity Provider の作成
gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}" \
  --display-name="GitHub Actions Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.actor=assertion.actor" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --project="${PROJECT_ID}"

# サービスアカウントへの権限バインディング（リポジトリを限定）
gcloud iam service-accounts add-iam-policy-binding \
  "${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${REPO}" \
  --project="${PROJECT_ID}"

# Provider と SA のリソース名を取得（GitHub Secrets に設定する値）
echo "WIF_PROVIDER:"
gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --location="global" \
  --workload-identity-pool="${POOL_ID}" \
  --format="value(name)" \
  --project="${PROJECT_ID}"

echo "WIF_SERVICE_ACCOUNT:"
echo "${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
```

### 3. GitHub Secrets の設定

GitHub リポジトリの **Settings > Secrets and variables > Actions** で以下を設定する：

| Secret 名 | 値 | 必須 |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | GCP プロジェクト ID | ✅ |
| `WIF_PROVIDER` | Workload Identity Provider のリソース名 | ✅ |
| `WIF_SERVICE_ACCOUNT` | サービスアカウントのメールアドレス | ✅ |
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
                                ├─► Cloud Run へデプロイ
                                ├─► ヘルスチェック（/health を最大5回）
                                └─► 失敗時 Slack 通知（SLACK_WEBHOOK_URL 設定時）
```

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
