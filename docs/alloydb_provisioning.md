# AlloyDB(pgvector) プロビジョニングガイド (Issue #28)

## 概要

TomorrowsMeal の層3（レシピ事例の検索・pgvectorベクトル検索、SPEC.md §3）は
AlloyDB for PostgreSQL 上で稼働する想定です。本ドキュメントでは、
`infra/terraform/` に用意したIaCコードを使って AlloyDB クラスタをプロビジョニングする手順、
pgvector拡張の有効化確認、Auth Proxy(IAM認証)経由の接続確認手順を説明します。

**このドキュメントに書かれた `terraform apply` / `terraform destroy` は、実装者（Claude）は実行していません。
すべてユーザーが手動で実行してください。**

---

## 0. 運用方針：常時起動しない（都度作成・削除）

**AlloyDBには「停止」機能がありません。** クラスタ・インスタンスが存在する限り、時間単位で課金され続けます
（停止してもコンピュートリソースの課金は止まりません。削除以外にコストを止める方法がありません）。

そのため本プロジェクトでは以下の運用方針を採用します:

1. 使う必要がある期間（例: ハッカソン審査週間、デモ直前の動作確認）だけ `terraform apply` でクラスタを作成する。
2. 使い終わったら `terraform destroy` で削除し、課金を止める。
3. **destroy するとデータは完全に失われる。** 次回 apply 時は空のデータベースから始まる前提とし、
   必要なら destroy 前に `pg_dump` でバックアップを取る（手順は後述）か、`scripts/migrate.py` で
   スキーマだけ再適用する（データは戻らないが構造は再現できる）。
4. **AlloyDBクラスタが存在しない間、Cloud Run側のDB機能（層1〜3のデータアクセス）は使えません。**
   `scripts/db_healthcheck.py` は接続先が存在しない/到達不能な場合、分かりやすいエラーで終了します
   （クラッシュはしません）。これは常時起動しない運用における正常な状態です。

この方針に合わせて、`infra/terraform/alloydb.tf` は以下のように「壊しやすく」設定されています:

- `lifecycle.prevent_destroy` は設定していません（誤ってdestroyできない事故より、意図した時にdestroyできないことの方が困るため）。
- `google_alloydb_cluster.main` に `deletion_policy = "FORCE"` を設定し、バックアップ・インスタンスが存在する状態でも destroy が通るようにしています。
- `continuous_backup_config.enabled = false`。都度destroyする運用ではバックアップ保存の継続課金が無駄になるため無効化しています。

---

## 1. 事前準備

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
```

`terraform.tfvars` を編集し、以下を実際の値に更新します:

- `project_id`（例: `agentic-ai-495701`）
- `cloud_run_service_account_email`（`.github/workflows/deploy.yml` がデプロイする
  Cloud Run サービス `tomorrows-meal-webapp` の実行サービスアカウント）
- 必要であれば `iam_db_users` にローカル開発者のGoogleアカウントを追加

初期adminパスワードは tfvars に平文で書かず、環境変数で渡します:

```bash
export TF_VAR_alloydb_admin_password="$(openssl rand -base64 24)"
```

---

## 2. `terraform apply`（ユーザーが手動実行）

```bash
cd infra/terraform
terraform init
terraform fmt -check
terraform validate
terraform plan    # 必ず内容を確認する（課金が発生するリソースの作成計画が表示される）
terraform apply   # 確認後、"yes" を入力して実行
```

apply完了後、以下で接続に必要な出力値を確認します:

```bash
terraform output alloydb_primary_instance_name
terraform output alloydb_primary_instance_ip
terraform output secret_connection_info_id
```

---

## 3. pgvector拡張の有効化とスキーマ適用（マイグレーション）

Terraformはクラスタ・インスタンスの作成のみを行います。pgvector拡張の有効化と
`app/models.py`（#13スキーマ）・層3の `recipe_snippets` テーブルの作成は
`scripts/migrate.py` で行います。

```bash
export ALLOYDB_INSTANCE_URI="$(terraform -chdir=infra/terraform output -raw alloydb_primary_instance_name)"
export ALLOYDB_DATABASE="tomorrows_meal"
# IAM認証ユーザー。初回はSecret Managerの初期adminパスワード(postgresユーザー)ではなく、
# 自分のGoogleアカウント or Cloud Run実行SAをIAM認証ユーザーとして使う（IAM DBユーザーはTerraformのiam.tfで登録済み）
export ALLOYDB_IAM_USER="developer@example.com"

uv run python scripts/migrate.py
```

成功すると以下が行われます:

1. `CREATE EXTENSION IF NOT EXISTS vector;` — pgvector拡張の有効化
2. `app/models.py` の全テーブル（users / inventories / meal_histories / feedbacks / meal_proposals / quality_score_logs）の作成
3. 層3専用テーブル `recipe_snippets`（`vector`型カラム + HNSWインデックス）の作成

### pgvector拡張の有効化確認

```sql
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
```

---

## 4. 接続確認（`scripts/db_healthcheck.py`）

```bash
uv run python scripts/db_healthcheck.py
```

- **AlloyDBインスタンスが存在しない/接続先未設定の場合**: 終了コード2でわかりやすいメッセージを出して終了します（正常な動作）。
- **接続に成功した場合**: `SELECT 1` の成功、pgvector拡張の有効化状況を表示し、終了コード0で終わります。
- **接続を試みたが失敗した場合**: 失敗理由を表示して終了コード1で終わります。

Auth Proxy（Language Connector, `google-cloud-alloydb-connector` + `pg8000`）を使い、
`enable_iam_auth=True` でIAM認証（パスワードレス）接続を行います。ローカル開発でも
`gcloud auth application-default login` 済みの認証情報でAuth Proxyと同じ接続方式が使えます。

---

## 5. Cloud Run からの接続

`infra/terraform/iam.tf` により、`.github/workflows/deploy.yml` がデプロイする
Cloud Run実行サービスアカウントに以下が付与されます:

- `roles/alloydb.client`（Auth Proxy経由の接続許可）
- `roles/serviceusage.serviceUsageConsumer`
- AlloyDB側のIAM認証データベースユーザーとしての登録（`google_alloydb_user`）

Cloud Run側は、環境変数 `ALLOYDB_INSTANCE_URI` / `ALLOYDB_DATABASE` を
Secret Manager（`secret_connection_info_id` の出力）またはCloud Runのenv設定から受け取り、
`scripts/db_healthcheck.py` と同様の接続ロジック（Language Connector + IAM認証）を
アプリケーション本体（`app/database.py` のPostgres対応時）でも利用する想定です。

> **現状のスコープ:** Issue #28 の時点では `app/database.py` は開発用SQLiteのまま変更していません
> （インスタンスが存在しない状態でアプリの通常起動が壊れることを避けるため）。
> AlloyDBへの本接続切り替え（`app/database.py` のPostgres対応）は、実インスタンスが安定して
> 利用できるようになった後の別チケットで対応することを推奨します。

---

## 6. `terraform destroy`（使い終わったらユーザーが手動実行）

### destroy前にバックアップを取る（推奨）

```bash
# IAM認証で接続し、pg_dumpでバックアップを取る場合の例
# (Auth Proxyのバイナリを別途起動する運用の場合。Language Connector経由のpg_dumpは
#  サポートされないため、gcloudのAuth ProxyバイナリまたはpsqlのIAM認証接続を使う)
gcloud alloydb clusters describe <CLUSTER_ID> --region=asia-northeast1

# 例: Cloud SQL Auth Proxy相当のAlloyDB Auth Proxyバイナリでローカルにポートフォワードしてからpg_dump
# ./alloydb-auth-proxy <INSTANCE_URI> --auto-iam-authn &
# pg_dump -h 127.0.0.1 -U developer@example.com -d tomorrows_meal -f backup_$(date +%Y%m%d).sql
```

バックアップが不要（データが失われても構わない）場合は、`scripts/migrate.py` の内容が
そのままスキーマ定義（#13相当）として残るため、次回apply後に再実行すればテーブル構造は再現できます。
**データそのもの（ユーザー・在庫・フィードバック履歴等）は再現されません。**

### destroy実行

```bash
cd infra/terraform
terraform plan -destroy   # 削除対象を必ず確認する
terraform destroy         # 確認後、"yes" を入力して実行
```

destroy後は `scripts/db_healthcheck.py` を実行すると接続エラー（終了コード1、または
`ALLOYDB_INSTANCE_URI` を環境変数から外していれば終了コード2）になりますが、これは正常です。

---

## 7. 概算コスト見積り（時間単位課金 × 使用想定時間）

> AlloyDBは常時起動を前提とせず、「使う時だけ apply → 使い終わったら destroy」する運用のため、
> 「1ヶ月あたり」ではなく「使用した時間 × 時間単価」で見積もります。以下は目安であり、
> 実際の料金は [AlloyDB料金ページ](https://cloud.google.com/alloydb/pricing) の最新情報を確認してください
> （2024年時点の一般的なレートに基づく概算。リージョン・タイミングにより変動します）。

| 項目 | 概算単価（東京リージョン想定） | 備考 |
| --- | --- | --- |
| コンピュート（2 vCPU, ZONAL） | 約 $0.36〜0.44 / 時間 | vCPU + メモリの合算。ZONAL(HAなし)は REGIONAL の約半分。 |
| ストレージ | 約 $0.13 / GB / 月 | 使用中のみ課金。数GB程度ならほぼ無視できる額。 |
| ネットワーク（プライベートIP内） | ほぼ無料 | Cloud Run ⇔ AlloyDB は同一VPC内通信のため、外部データ転送料はかからない想定。 |

**使用シナリオ別の概算:**

- **動作確認1回（1〜2時間 apply→destroy）**: 約 $0.5〜1 程度。
- **ハッカソン審査週間（1日8時間 × 5日 = 40時間 起動）**: 約 $15〜20 程度。
- **うっかり1週間（168時間）起動したままにした場合**: 約 $60〜75 程度。← これを避けるため、都度destroyする運用を徹底する。

**コスト管理のポイント:**

- 使い終わったら**その日のうちに `terraform destroy` する**運用を徹底する。
- GCPの予算アラート（Budget Alerts）を別途設定しておくと、想定外の起動し続けに気づきやすい。
