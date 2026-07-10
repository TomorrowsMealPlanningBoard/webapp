# Cloud Run コールドスタート対策 調査・提案

対象サービス: `tomorrows-meal-webapp` / リージョン `asia-northeast1`
背景: `/health` 初回アクセスが約12秒（コールドスタート）。審査期間（2026/7/13〜7/24）に審査員が初めてURLを開いたときに12秒ハングし「動かない」と誤判定されるリスクがある。

> 本ドキュメントは **調査と提案のみ**。terraform差分は「提案」であり、適用はしていない。

---

## 1. 現在の設定（Terraform 実測値）

`infra/terraform/cloud_run.tf` および `variables.tf` / `terraform.tfvars` より。

| 項目 | 現在値 | 定義箇所 |
|---|---|---|
| min_instance_count | **0** | `variables.tf` `cloud_run_min_instances` default=0（tfvarsでも0） |
| max_instance_count | **1** | `cloud_run_max_instances` default=1（commit 57a5491 で 10→1） |
| CPU | **1** vCPU | `cloud_run_cpu` |
| memory | **1Gi** | `cloud_run_memory` |
| CPU allocation | **request-based（default）** | `cpu_idle` 未指定 → アイドル時 CPU スロットリング |
| startup CPU boost | **無効（未指定）** | `startup_cpu_boost` の設定なし |
| concurrency | **未指定（v2 default = 80）** | `max_instance_request_concurrency` なし |
| startup probe | **未設定** | デフォルトの TCP probe（PORT 到達）に依存 |
| 公開 | allUsers に `roles/run.invoker`（未認証アクセス可） | `public_invoker` |

補足:
- `image` は `lifecycle.ignore_changes` で Terraform 管理外（deploy.yml が更新）。**min/max/CPU/memory は Terraform で変更可能**。
- Cloud Run v2 のデフォルトは request-based CPU（`cpu_idle=true` 相当）。つまり **リクエストを処理していない間 CPU がほぼ 0 に絞られる**ため、`min_instances=1` にしても「アイドル待機インスタンス」は CPU 割り当てが薄く、起動処理（import）を待機中に済ませておけない点に注意（後述の案B参照）。

---

## 2. コールドスタート12秒の主因（推定）

優先度順の推定要因:

1. **min_instances=0（最大要因）**
   審査員の初回アクセス時は常にゼロからのコンテナ起動になる。これがある限り、他をどれだけ最適化しても初回は必ずコールドスタートが発生する。

2. **Python 依存の import が非常に重い**
   `requirements.txt` は `google-adk==2.3.0` が `google-cloud-aiplatform`, `google-cloud-bigquery/bigtable/spanner/pubsub/discoveryengine/dataplex/speech/...` など **大量の google-cloud-* SDK と grpcio/protobuf/pyarrow** を芋づる式に持ち込んでいる。
   `app/main.py` はトップレベルで `from .agents.orchestrator import MealOrchestrator` 等をまとめて import しており、**FastAPI アプリ起動＝この重量級ツリー全部を import** する。`grpcio` / `pyarrow`(24MB級) / `protobuf` / `cryptography` の import だけで数秒規模になりうる。

3. **イメージが軽量化されていない**
   `python:3.11-slim` ベースで `pip install --no-cache-dir` 後に `COPY . .`。マルチステージや不要ファイル除外（`.dockerignore`）が無く、pyarrow/grpcio 等のバイナリwheel込みでイメージが大きめ → pull/展開に時間。

4. **リクエスト時のクライアント初期化は「主因ではない」**
   `genai.Client(vertexai=True, ...)`（recipe_generator/vision_analyzer/voice_session）や `firestore.Client()`（firestore_store/daily_limit）は **関数内で遅延生成**されており、import 時・`/health` 時には走らない。つまり `/health` の12秒は「コンテナ起動＋アプリ import」がほぼ全て。ここは既に良い設計。

結論: **12秒の大半は「ゼロからのコンテナ起動 ＋ google-adk 系の重い import」**。`/health` は DB もAIも触らないので、それ以外の起動コストがそのまま出ている。

---

## 3. 対策の選択肢（コスト影響つき）

### コスト概算の前提（asia-northeast1, Cloud Run 第2世代, request-based でアイドルも課金される min_instances 分）

min_instances で常時確保されるインスタンスは、**リクエストが無くてもアイドル料金**が発生する（idle 単価）。asia-northeast1 の概算単価（2025時点の公開料金・目安）:

- vCPU: 約 **$0.0000240 / vCPU 秒**（アクティブ）、アイドルは約 **$0.0000024 / vCPU 秒**（1/10）
- Memory: 約 **$0.0000025 / GiB 秒**（アクティブ）、アイドルは約 **$0.00000025 / GiB 秒**

min_instances=1（1 vCPU / 1GiB）を **アイドル単価で常時1台**確保した場合の概算:

- 1秒あたり: `1 * 0.0000024 + 1 * 0.00000025 ≈ $0.00000265 /秒`
- 1日: `0.00000265 * 86,400 ≈ $0.229 /日`
- 審査期間 12日（7/13〜7/24）: **約 $2.7**
- 30日換算: **約 $6.9 /月**

> 注: 実際にはリクエスト処理中はアクティブ単価に上がるが、審査トラフィックは軽微なので支配項はアイドル。無料枠（月 180,000 vCPU秒・360,000 GiB秒）も存在するため、**実請求はさらに下振れし、ほぼ無料枠内に収まる可能性が高い**。いずれにせよ **$300 クーポン残高に対して誤差レベル**。

---

### 案A: min_instances = 1（常時1台）★推奨

- 効果: **コールドスタート完全解消**。審査員の初回アクセスも即応答。
- コスト: 上記の通り **審査12日で約$2.7、月換算約$7**（無料枠考慮で実質さらに小）。
- リスク: max=1 のままなので暴走課金なし。#89 の日次上限とも独立（後述）。
- 欠点: 審査後に戻し忘れると微少コストが継続 → 案Cで運用カバー。

### 案B: CPU always-allocated + startup CPU boost

- `cpu_idle=false`（CPU always-allocated）にすると、min_instances のアイドル台がフル CPU を持ち続けるため、起動処理を待機中に完了させて温存できる。`startup_cpu_boost=true` は**起動時のみ CPU を増強**し、重い import を高速化（コールドスタート自体の秒数短縮）。
- 効果: min=0 のままでも起動を数秒短縮できる可能性。min=1 と併用すると盤石。
- コスト: `cpu_idle=false` はアイドルもアクティブ単価課金になり **min_instances と併用すると案Aの約10倍**（それでも月$70規模、$300内）。**boost だけなら常時課金増はほぼ無い**（起動時のみ）。
- 推奨: **`startup_cpu_boost=true` は低コストで効くので案Aに併用**。`cpu_idle=false` は今回はコスト対効果が悪いので不要。

### 案C: 審査期間だけ min=1、終了後 0 に戻す運用（コスト最小）★併用推奨

- 7/13 直前に `cloud_run_min_instances=1` へ、7/24 後に `0` へ戻す（tfvars 変更＋apply、または gcloud で一時上書き）。
- 効果: 案Aと同じ体感、かつ**課金は審査期間の約$2.7のみ**。
- 実務: 案A＋「戻し作業をカレンダー登録」で運用。**本命はこれ（案A を期間限定で適用）**。

### 案D: デモ/審査直前の手動ウォームアップ（curl 連打）

- min=0 のまま、審査開始直前に `curl https://.../health` を数回叩いてインスタンスを温める。
- 効果: 一時的にはコールドスタート回避。ただし **Cloud Run はアイドル数分でインスタンスを落とす**ため、審査員が「いつ開くか」を読めない審査期間中は**信頼性が低い**。cron で数分おきに叩けば維持できるが、外部スケジューラが必要で運用が煩雑。
- 位置づけ: **単独では非推奨。緊急の保険**。

### その他: 起動高速化（恒久対策・任意）

- **遅延 import**: `app/main.py` トップの `from .agents.orchestrator import ...` 等を、実際に使うエンドポイント内 import に移す。少なくとも `/health` の起動コストを下げ、`GenerativeModel`/genai クライアント系ツリーの import を遅延できる。ただし ADK ツリーは結局どこかで import されるため、**初回リクエスト種別によっては効果限定的**。
- **`.dockerignore` 追加＋不要依存削減**: `__pycache__`, テスト, `docs/` 等を除外しイメージ縮小。`google-adk` の依存の重さは削れないが、pull 時間は短縮。
- 効果は案Aほど確実でないため、**審査対策としては案A/Cを優先、恒久改善として別チケット化**を推奨。

---

## 4. #89（1日リクエスト上限）との競合有無

**競合しない。**

- `app/daily_limit.py` の日次上限（`propose:10 / vision:5 / voice_seconds:180`）は **Firestore 上の「ユーザー×アクション×日付」カウンタ**によるアプリケーション層のレート制限。認証ユーザー単位で AI 呼び出し回数を絞る仕組み。
- 一方 min/max_instances は **Cloud Run のインフラ層のスケーリング**設定。両者は次元が異なる。
- min_instances=1 にしても「AI を叩く回数」は増えないため、課金暴走防止（#89）の効果は一切損なわれない。むしろ max_instances=1 が同時実行を1台に抑えており、AI コストの上限は日次カウンタ側で担保されたまま。
- したがって **案A/Cを適用しても #89 の防御はそのまま有効**。

---

## 5. 推奨（優先順位つき）

1. **【第1候補】案A を案C の運用でラップ**: `cloud_run_min_instances = 1` を **審査期間だけ**適用し、終了後 0 に戻す。コスト約$2.7、コールドスタート完全解消、#89 と無競合。
2. **【併用】案B の `startup_cpu_boost = true`**: 低コストで起動短縮。min=1 と併せて盤石に。将来 min=0 に戻したときの初回も速くなる。
3. **【任意・別チケット】起動高速化**: 遅延 import ＋ `.dockerignore`。恒久的なコールドスタート短縮。
4. 案D（手動curl）は**保険**。単独運用はしない。

---

## 6. Terraform 差分の「提案」（適用しないこと）

### 6-1. min_instances を審査期間だけ 1 に（案A/C）

`infra/terraform/terraform.tfvars`:

```hcl
# 審査期間（7/13〜7/24）だけ 1。終了後は 0 に戻す。
cloud_run_min_instances = 1   # ← 0 から変更
cloud_run_max_instances = 1   # 変更なし（暴走防止のため据え置き）
```

変数のデフォルトは変えず、tfvars だけで期間限定運用するのが安全（`variables.tf` は既に `cloud_run_min_instances` を持つので、`cloud_run.tf` の変更は不要）。

### 6-2. startup CPU boost の追加（案B・低コスト）

`infra/terraform/cloud_run.tf` の `template` ブロックに以下を追加する提案:

```hcl
  template {
    service_account = google_service_account.cloud_run.email

    # 起動時のみ CPU を増強し、重い import（google-adk 系）を高速化。
    # 常時課金は増えず、コールドスタート秒数のみ短縮する。
    annotations = {
      "run.googleapis.com/startup-cpu-boost" = "true"
    }

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }
    # ... containers { ... } は現状のまま
  }
```

> 注: provider バージョンによっては `containers.resources` 側に `startup_cpu_boost = true` 引数として書ける場合がある（`google_cloud_run_v2_service` の対応バージョン依存）。apply 前に `terraform plan` で受理される書式を確認すること。

### 6-3.（任意・非推奨）CPU always-allocated（案B強）

コスト増（min=1 と併用で約10倍）のため今回は非推奨。参考:

```hcl
    containers {
      resources {
        limits    = { memory = var.cloud_run_memory, cpu = var.cloud_run_cpu }
        cpu_idle  = false   # ← アイドル時も CPU 維持（コスト増、今回は不要）
      }
    }
```

---

## 7. 適用・撤去の運用メモ（提案）

- 適用（7/13 直前）: tfvars を `cloud_run_min_instances = 1` にして CI/CD で apply。
- 撤去（7/24 後）: `0` に戻して apply（戻し忘れ防止にカレンダー登録）。
- 緊急時: `gcloud run services update tomorrows-meal-webapp --region=asia-northeast1 --min-instances=1` で即時反映（Terraform ドリフトに注意、後で tfvars を合わせる）。
</content>
</invoke>
