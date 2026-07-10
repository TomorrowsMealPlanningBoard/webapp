# Issue #82 — Memory Bank 日本語 embedding 精度 実機検証レポート

- 検証日: 2026-07-10
- 対象 Agent Engine (Memory Bank): `projects/agentic-ai-495701/locations/us-central1/reasoningEngines/6163772575114592256`
- 検証者スコープ: 専用 `user_id`（`eval_ja_82` / `eval_en_82`）で分離。検証後に投入メモリは全件削除済み（本番データ非汚染）。
- 検証方法: `google-cloud-aiplatform` SDK が venv で import エラー（`google.api.field_behavior_pb2` 競合）のため、gcloud アクセストークン + Memory Bank REST API（`v1beta1`）で直接検証。

## 結論（TL;DR）

**要対応（重大）。** 現在の Agent Engine は **`gemini-embedding-001` が未指定**で、デフォルトの
`text-embedding-005`（英語専用）で動作している。**日本語の好み学習（ループA）は実機で機能していない。**
デモ撮影前に Agent Engine を `gemini-embedding-001` 明示指定で再作成する必要がある。

---

## フェーズ1: 現在の embedding 設定

Agent Engine の定義を REST で照会した結果（抜粋）:

```json
{
  "displayName": "tomorrows-meal-memory-bank",
  "createTime": "2026-07-07T13:58:47Z",
  "contextSpec": {
    "memoryBankConfig": {
      "generationConfig": { "model": "gemini-3.5-flash" }
    }
  }
}
```

- `contextSpec.memoryBankConfig` に **`similaritySearchConfig` ブロックが存在しない** → `embedding_model` 未設定。
- レスポンス全文を `embedding` / `similarity` で grep しても該当なし（設定なし＝デフォルト適用）。
- デフォルト embedding は `text-embedding-005`（英語専用）。→ Issue #82 が懸念していたリスクが**そのまま現存**。

補足（コードベース側の状態）:
- `app/agents/memory_bank_client.py` のモジュール docstring 自体が「Agent Engine 作成時に
  `gemini-embedding-001` を明示指定すること」を必須要件として明記している。
- しかし `infra/terraform/` には Agent Engine を作成する Terraform リソース／プロビジョニング
  スクリプトが**存在しない**（`memory_bank_agent_engine_id` は変数として Cloud Run に渡すだけ）。
  つまりこの Engine は手動作成され、その際に embedding_model 指定が漏れたと推定される。

## フェーズ2: 日本語FBでの実機検索精度

### 投入した日本語FB（`eval_ja_82`、8件・意味的に分類可能な多様なFB）

Memory Bank が自動抽出した fact（generation 後）:
1. 味付けが薄い／もう少し塩気が欲しい
2. 子供が野菜を残す
3. 揚げ物は胃もたれするので控えめに
4. 家族は甘い味付け（照り焼き・煮物）が好み
5. 平日は15分で時短調理を好む
6. 魚料理（焼き魚）を増やしたい
7. 辛い料理が苦手
8. 週末に作り置きを好む

（generation は日本語で正しく機能。問題は後段の類似度検索。）

### 類似度検索の結果（日本語クエリ）

| クエリ | 期待ヒット | 実際のTop3 | 判定 |
|---|---|---|---|
| 味付けの濃さについて教えて | 塩/薄い | 子供・野菜 / 作り置き / 揚げ物 | ✗ |
| 子供の好き嫌い | 子供・野菜 | 子供・野菜 / 作り置き / 揚げ物 | △(偶然1位) |
| 胃に優しい料理 | 揚げ物 | 子供・野菜 / 作り置き / 揚げ物 | ✗ |
| 忙しい日の料理 | 時短 | 子供・野菜 / 作り置き / 揚げ物 | ✗ |
| 魚が食べたい | 魚 | 子供・野菜 / 作り置き / 揚げ物 | ✗ |

**決定的な異常: すべてのクエリで Top3 が「同一メモリ・同一順序」を返し、`distance`（類似度スコア）が
レスポンスに一切含まれない。** クエリの意味を全く反映していない＝日本語ベクトルが機能不全。

### 対照実験（英語 `eval_en_82`）

同じ Engine に**英語**FBを投入し英語クエリで検索すると、`distance` が返り意味順に並ぶ:

| クエリ | Top1 (distance) | 判定 |
|---|---|---|
| about saltiness of seasoning | saltier/bland (0.627) | ✓ |
| children food preferences | child/vegetables (0.830) | ✓ |
| I want to eat fish | fish dishes (0.666) | ✓ |

→ Engine 自体は正常。**「日本語だけ」semantic search が破綻**していることを確定。
これはデフォルト `text-embedding-005`（英語専用）による日本語ベクトル品質の劣化が原因。

## フェーズ3: 精度所見と推奨対応

### 所見
- **日本語での好み学習ループA（＝デモの心臓部）は現状の Engine では機能しない。**
  意味的に近いFBがクエリで引けないため、Context Retriever が誤った／無関係な好みを注入する。
- generation（FB→fact 抽出）は日本語で問題なし。破綻しているのは類似度検索（embedding）のみ。

### 推奨対応（必須）: Agent Engine を `gemini-embedding-001` 明示で再作成

破壊操作は避ける方針のため既存 Engine は削除せず、**新規 Engine を並行作成**して
`terraform.tfvars` の `memory_bank_agent_engine_id` を差し替える。

再作成時に指定する設定（`contextSpec.memoryBankConfig`）:

```json
{
  "memoryBankConfig": {
    "generationConfig": { "model": "gemini-3.5-flash" },
    "similaritySearchConfig": { "embeddingModel": "gemini-embedding-001" }
  }
}
```

REST での作成例（location=us-central1）:

```bash
PROJECT=agentic-ai-495701
TOKEN=$(gcloud auth print-access-token)
curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/${PROJECT}/locations/us-central1/reasoningEngines" \
  -d '{
    "displayName": "tomorrows-meal-memory-bank-ja",
    "description": "Memory Bank (gemini-embedding-001, 多言語/日本語対応)",
    "contextSpec": {
      "memoryBankConfig": {
        "generationConfig": {"model": "gemini-3.5-flash"},
        "similaritySearchConfig": {"embeddingModel": "gemini-embedding-001"}
      }
    }
  }'
# 返却された reasoningEngines/<NEW_ID> を terraform.tfvars の
# memory_bank_agent_engine_id に反映し、Cloud Run を再デプロイ。
```

Python SDK（`vertexai.agent_engines` / `client.agent_engines.create`）でも
`context_spec.memory_bank_config.similarity_search_config.embedding_model="gemini-embedding-001"`
を指定すれば同等（本 venv は SDK import 不可のため今回は REST を採用）。

所要時間の目安:
- Engine 作成: 数分（作成は非同期、上記 create は即レスで ID 返却、内部プロビジョニングが数分）。
- `terraform.tfvars` 反映 + Cloud Run 再デプロイ: 数分。
- 再検証（日本語FB投入→30秒程度のインデックス待ち→クエリ）: 5〜10分。
- 合計 15〜25分程度でデモ前に是正可能。

### 代替（時間がない場合の緩和策、非推奨）
- custom topics + few-shot で generation を寄せても、**破綻しているのは検索側**なので
  embedding を変えない限り根治しない。→ 緩和にならない。**Engine 再作成が唯一の実効策。**

### デモへの影響
- 是正しない場合: 「育つAI（ループA）」を日本語で見せると、無関係な好みが引かれる／毎回同じ結果になり、
  デモの主張が崩れる**重大リスク**。
- 是正後は再検証で `distance` が返り意味順に並ぶことを必ず確認すること（本レポートの英語対照が期待挙動）。

---

## フェーズ4: 是正の実施と再検証（2026-07-10 完了）

### 実施内容
1. `gemini-embedding-001` を明示指定した新しい Agent Engine を作成。
   - **重要な追加発見**: 作成APIは model を**フルパス**
     （`projects/{p}/locations/us-central1/publishers/google/models/{model}`）で要求する（短縮名は `Invalid model name`）。
   - **さらに重大な発見**: 生成モデルは **us-central1 で実際に利用可能なものに限る**。
     `gemini-3.5-flash` / `gemini-3.1-flash-lite` は publisher カタログには存在する（GET 200）が、
     **us-central1 の `generateContent` は 404**。この状態で Engine を作ると
     `memories:generate` が「Publisher model ... was not found」で**非同期失敗し、fact が1件も保存されない**。
     （旧Engineの `gemini-3.5-flash` 生成も同様に失敗していたと推定される。）
   - `generateContent` 実測で **us-central1 で利用可能なのは `gemini-2.5-flash`**（200）と判明したため、これを採用。
2. 最終構成で Engine を再作成:
   - 生成: `gemini-2.5-flash`（us-central1 で利用可）
   - embedding: `gemini-embedding-001`（多言語）
   - **新 Engine ID: `1223394152633335808`**（旧 `6163772575114592256` は削除せず残置）
3. `infra/terraform/terraform.tfvars` の `memory_bank_agent_engine_id` を新IDに差し替え。

### 再検証結果（日本語 semantic search）— ✅ 正常化を確認

日本語FB 8件を投入（非同期 generate オペレーションの `done` を全件ポーリングで確認、エラー0）。
7件のfactが保存され、日本語クエリで **distance が返り、意味的に正しい Top1** を得た:

| クエリ | Top1 (distance) | 判定 |
|---|---|---|
| 味付けの濃さについて | more saltiness (0.425) | ✓ |
| 胃に優しい料理 | limit fried foods (0.472) | ✓ |
| 忙しい日の料理 | weekday quick cooking (0.403) | ✓ |
| 魚が食べたい | more fish dishes (0.405) | ✓ |
| 子供の好き嫌い | child leaves vegetables (0.425) | ✓ |

**5/5 で意味的に正しい結果**。破綻時の「全クエリ同一・distance欠落」は解消。
（fact は generation により英語で正規化保存されるが、日本語クエリのembeddingが正しくマッチするため
検索は機能する＝ループAは日本語で成立。）検証後、投入メモリは全件削除（本番非汚染）。

### 残課題（フォローアップ）
- **Agent Engine を作成する IaC/スクリプトが未整備**（手動作成でモデル指定漏れが起きた根本原因）。
  再発防止のため、`gemini-2.5-flash` + `gemini-embedding-001` を明示する作成スクリプトを
  `scripts/` に用意して Terraform 管理下に置くのが望ましい（別チケット化推奨）。
- Cloud Run 再デプロイ後、本番の `/api/feedback` → 好み学習が日本語で機能するかを実アプリでも確認する。

---

## 再現用スクリプト
検証スクリプトはスクラッチ領域に保存（`mb_verify.py` / `mb_scores.py` / `cleanup.py`）。
Memory Bank REST の要点:
- 生成: `POST {engine}/memories:generate` に `directContentsSource.events[].content` と
  `scope={app_name, user_id}`。
- 検索: `POST {engine}/memories:retrieve` に `scope` と
  `similaritySearchParams={searchQuery, topK}`。日本語では `distance` が欠落する現象を確認。
