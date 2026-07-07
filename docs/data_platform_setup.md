# データ基盤セットアップガイド（Memory Bank / Firestore）

Epic #75 のアーキテクチャ移行後、TomorrowsMeal のデータ基盤は以下の2種類のみで構成される
（AlloyDB(pgvector)・RAG Engineは不採用。歴史記録は
[`infra/terraform-archive/alloydb-deprecated/`](../infra/terraform-archive/alloydb-deprecated/) を参照）。

| データ | 基盤 | 用途 |
| --- | --- | --- |
| 層1（アレルギー等）・層2（構造化FB）・層3'（外部レシピソース） | Firestore | 決定的フィルタ・構造化データ（Issue #76, #78） |
| 層3（ユーザーFBの好み学習） | Agent Platform Memory Bank | 会話/FBからの自動学習（Issue #77） |

いずれも Cloud Run 実行サービスアカウントの IAM（ADC）のみで接続できる。AlloyDBのような
Auth Proxy・接続プールの自前運用や、Secret ManagerでのDBパスワード管理は不要（SPEC.md §6.4）。

## IAMロール

Cloud Run 実行サービスアカウントに以下のロールを付与する。

```bash
# Firestore（層1/層2/層3'）
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SERVICE_ACCOUNT_EMAIL" \
  --role="roles/datastore.user"

# Memory Bank（層3、Agent Platform / Vertex AI Agent Engine）
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SERVICE_ACCOUNT_EMAIL" \
  --role="roles/aiplatform.user"
```

AlloyDB採用時に必要だった `roles/alloydb.client` / `roles/serviceusage.serviceUsageConsumer` は不要になった。
Secret Manager上のAlloyDB管理者パスワード（`tomorrows-meal-db-admin-password`等）が存在する場合は削除すること。

## 環境変数

| 変数 | 用途 | デフォルト |
| --- | --- | --- |
| `USE_FIRESTORE` | `true` でFirestoreストア（層1/層2）を有効化 | 未設定時はSQLite/AlloyDB共存構成 |
| `USE_MEMORY_BANK` | `true` でMemory Bankクライアント（層3）を有効化 | 未設定時は `InMemoryVectorSearchClient` |
| `MEMORY_BANK_AGENT_ENGINE_ID` | Memory Bank用Agent EngineのID | 必須（`USE_MEMORY_BANK=true`時） |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | 両基盤共通の接続先 | - |

## Memory Bank プロビジョニング時の必須設定

Agent Engine（Memory Bankのバックエンド）を作成する際、
`context_spec.memory_bank_config.similarity_search_config.embedding_model` に
**`gemini-embedding-001`（多言語対応）を明示指定すること。**
デフォルトの `text-embedding-005` は英語専用で、日本語の自由記述FBが正しくベクトル化されず
好み学習ループ（ループA）が機能しない事故になる。実機での確認・調整は Issue #82 で対応する。

## 疎通確認

```bash
export GOOGLE_CLOUD_PROJECT="<project-id>"

# Firestore
uv run python scripts/db_healthcheck.py --target firestore

# Memory Bank（Agent Engineプロビジョニング後）
export MEMORY_BANK_AGENT_ENGINE_ID="<agent-engine-id>"
uv run python scripts/db_healthcheck.py --target memory_bank
```

## コスト見積り（AlloyDB時間課金からの変更点）

AlloyDB採用時は「クラスタが起動している限り時間単位で課金され続ける」ため、
都度 `terraform apply` / `terraform destroy` する運用が必要だった。新基盤は完全従量課金で、
起動・停止の運用が不要になる。

| 項目 | 単価 | 備考 |
| --- | --- | --- |
| Memory Bank ストレージ | $0.30/GiB-月（revisions含む） | 小規模（ユーザーあたり数百memory）なら実質無視できる額 |
| Memory Bank 読み取り | 300万回ごとに vCPU-h ($0.085) | ハッカソン規模では閾値に届かない想定 |
| Memory Bank 書き込み | 100万回ごとに vCPU-h ($0.085) | 同上。billing開始は2026/9/1〜 |
| Firestore | 読み書き回数・保存量に応じた従量課金 | 層1/2/3'は小規模データのため実質無視できる額 |

詳細な調査結果はEpic #75本文を参照。
