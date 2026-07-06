# Cloud Trace による可観測性ガイド

## 概要

TomorrowsMeal では OpenTelemetry を使ってマルチエージェントの処理をトレースします。
環境に応じて出力先が自動的に切り替わります。

| 環境 | `GOOGLE_CLOUD_PROJECT` | 出力先 |
|------|------------------------|--------|
| ローカル開発 | 未設定 | ターミナル（ConsoleSpanExporter） |
| Cloud Run / GCP | 設定済み | Cloud Trace（CloudTraceSpanExporter） |

---

## トレースされるスパン

### 1. オーケストレーター（`tomorrows_meal.orchestrator`）

| スパン名 | タイミング | 主な属性 |
|----------|-----------|---------|
| `collect_context` | Context Retriever 実行中 | `user_id`, `mood_tags`, `duration_ms`, `allergen_count`, `similar_snippets_count` |
| `collect_vision` | Vision Analyzer 実行中 | `used_image`, `ingredient_count`, `duration_ms` |
| `generate` | Recipe Generator 実行中 | `model_name`, `ingredient_count`, `duration_ms` |
| `review` | Reviewer ループ実行中 | `user_id`, `max_retries`, `retry_counts`, `total_retries`, `duration_ms` |

### 2. Recipe Generator（`tomorrows_meal.recipe_generator`）

| スパン名 | タイミング | 主な属性 |
|----------|-----------|---------|
| `llm_generate_recipes` | Gemini API 呼び出し | `model_name`, `prompt_name`, `latency_ms`, `retry_count`, `recipe_count`, `error`, `error_message` |

### 3. Reviewer（`tomorrows_meal.reviewer`）

| スパン名 | タイミング | 主な属性 |
|----------|-----------|---------|
| `review_recipe_with_retries` | 差し戻しループ実行中 | `recipe_title`, `max_retries`, `approved`, `attempts`, `rejection_reasons`, `fallback_used` |

差し戻しが発生した場合、スパンに `recipe_rejected` イベントが追加され、差し戻し理由が記録されます。

---

## ローカルでのトレース確認

### 前提

- `GOOGLE_CLOUD_PROJECT` を**設定しない**（未設定の場合に ConsoleSpanExporter が有効になる）

### 起動方法

```bash
# Docker Compose で起動
docker compose up

# または uv run で直接起動
uv run uvicorn app.main:app --reload
```

起動後に `/api/propose` または `/api/suggest` を呼び出すと、ターミナルに JSON 形式のスパンが出力されます。

### 出力例

```json
{
    "name": "collect_context",
    "context": {
        "trace_id": "0x...",
        "span_id": "0x..."
    },
    "parent_id": "0x...",
    "start_time": "2026-07-06T10:00:00.000000Z",
    "end_time": "2026-07-06T10:00:01.234000Z",
    "attributes": {
        "user_id": "default_user",
        "mood_tags": "['肉料理']",
        "duration_ms": 1234.5,
        "allergen_count": 2,
        "similar_snippets_count": 3
    }
}
```

---

## Cloud Trace（GCP）での確認

### 前提

- Google Cloud プロジェクトで Cloud Trace API が有効になっていること
- `GOOGLE_CLOUD_PROJECT` 環境変数にプロジェクト ID が設定されていること
- Cloud Run のサービスアカウントに `roles/cloudtrace.agent` が付与されていること

### 設定（Cloud Run）

```bash
# Cloud Run のサービスアカウントに Cloud Trace 書き込み権限を付与
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SERVICE_ACCOUNT_EMAIL" \
  --role="roles/cloudtrace.agent"
```

### 確認手順

1. [Google Cloud Console](https://console.cloud.google.com/) を開く
2. 左メニューから **[オペレーション] > [Cloud Trace]** を選択
3. **[トレースリスト]** タブを開く
4. アプリケーションの `/api/propose` エンドポイントにリクエストを送る
5. 数秒後にトレースが表示される

### トレースの見方

- **ルートスパン**: `meal_planning_workflow`（Workflow 全体の処理時間）
- **並列スパン**: `collect_context` と `collect_vision` が同時に実行される様子が確認できる
- **逐次スパン**: `generate` → `review` の順に実行される
- **差し戻しイベント**: `review_recipe_with_retries` スパン内の `recipe_rejected` イベントで差し戻し理由を確認できる
- **LLM レイテンシ**: `llm_generate_recipes` スパンの `latency_ms` 属性で実測値を確認できる

---

## ローカル開発時の注意事項

- `PYTEST_CURRENT_TEST` 環境変数が設定されている場合（pytest 実行中）、TracerProvider の初期化はスキップされます。これはテストへの副作用を防ぐためです。
- `GOOGLE_CLOUD_PROJECT` が未設定の場合でも、ConsoleSpanExporter によってターミナルにスパンが出力されます。出力量が多い場合は環境変数 `OTEL_LOG_LEVEL=error` を設定してスパン出力を抑制できます。
