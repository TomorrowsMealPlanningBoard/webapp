---
name: implement-ticket
description: チケットURLを受け取り、SPEC.mdとの整合性チェック → AC確認・補完 → 実装 → テスト → PR作成まで自律実行する。「このチケットを実装して」と言われたときに必ず使うこと。
---

# implement-ticket

## 概要

チケットURLを受け取り、以下の順序で自律実行する：

1. **SPEC整合性チェック**（最重要・必ずやる）
2. AC確認・補完
3. 実装
4. テスト
5. PR作成

**開始時に宣言する：** 「implement-ticket スキルを開始します。」

---

## Phase 0: SPEC整合性チェック（実装着手前に必須）

> このフェーズは「今あるチケットで本当に十分か」を問い直すためにある。
> チケットのACが正しくても、チケット自体が存在しないケースを発見するのが目的。

### 0-1. チケットとSPECを読む

```bash
# SPEC.mdを読む
cat /home/tutti/repos/gcloud-devops-aiagent-hackathon2/SPEC.md
```

GH CLIでチケット本文を取得する：

```bash
gh issue view <issue番号> --repo TomorrowsMealPlanningBoard/webapp
```

### 0-2. SPECとチケット群の全体マップを照合する

SPEC.mdに記載されている機能・データフロー・エージェント連携を洗い出し、以下の観点でギャップを探す：

**チェック観点（必ず全部見る）：**

1. **データの出口と入口は繋がっているか？**
   - 例：Vision Analyzer AgentがJSONを返す → それをRecipe Generator Agentが受け取る経路がチケット化されているか
   - 例：冷蔵庫認識結果がstateに保持され、/api/suggestのリクエストに含まれる実装がチケット化されているか

2. **フロントエンドとバックエンドの境界は両側チケット化されているか？**
   - バックエンドAPIが実装されていても、フロントがそれを呼ぶ実装のチケットが存在するか（逆も）

3. **エージェント間の繋ぎ込みはチケット化されているか？**
   - SPEC §5.2のマルチエージェント処理フローの各ステップ（Context Retriever → Vision Analyzer → Recipe Generator → Reviewer）に対応するチケットが存在するか

4. **ループA・ループBの実装は網羅されているか？**
   - SPEC §4の「フィードバック → 嗜好プロファイル更新 → 次回提案への反映」の各ステップ
   - GitHub Actions eval、自動起票、Cloud Traceの可観測性

5. **層1の決定的フィルタは独立してチケット化されているか？**
   - アレルギー・禁止食材の除外が確率的処理（ベクトル検索）に混入していないか

6. **今回の実装が「将来の機能」の前提になっていないか？**
   - 現チケットで作るAPIが、後続チケットの入力として正しく設計されているか
   - モック実装のまま放置される箇所がないか（または意図的にモックで良いか明記されているか）

### 0-3. 発見した問題を報告する

ギャップが見つかった場合、以下の形式でユーザーに報告し、**判断を仰ぐ**：

```
## SPEC整合性チェック結果

### 今回のチケット（#XX）に問題はありません

### ただし、以下のギャップを発見しました

#### ギャップ1: [タイトル]
- **問題**: [何が繋がっていないか]
- **SPECの根拠**: §X.X に「...」と記載
- **現状**: 対応チケットが存在しない / 既存チケット #YY のACに含まれていない
- **推奨対応**: 新チケット作成 / 既存チケット #YY のACに追記

#### ギャップ2: ...

### 選択してください
A. 発見したギャップを先に対処する（チケット作成 / AC追記）
B. 今回のチケット実装を優先し、ギャップは別途対応する
C. このギャップは許容する（理由: ...）
```

ユーザーの判断を受けてから次のフェーズへ進む。ギャップがない場合はその旨を報告してPhase 1へ進む。

---

## Phase 1: AC確認・補完

### 1-1. チケットのACを読む

```bash
gh issue view <issue番号> --repo TomorrowsMealPlanningBoard/webapp
```

### 1-2. ACの品質チェック

以下の基準でACを評価する：

- 「誰が読んでも完了かどうか判定できる」粒度か
- バックエンドAPIの場合：どのエンドポイント・レスポンス形式か明記されているか
- フロントエンドの場合：どの画面・操作・表示が変わるか明記されているか
- テストが書けるレベルの具体性があるか
- エラーケースの扱いが定義されているか

### 1-3. ACがない or 不十分な場合

ACの案を作成してユーザーに提示し、承認を得てからチケットに記載する：

```bash
# 承認後にチケットを更新
gh issue edit <issue番号> --repo TomorrowsMealPlanningBoard/webapp --body "..."
```

**実装を開始しない。** ACが確定するまでここで止まる。

### 1-4. チケットステータスをIn Progressに更新

```bash
gh issue edit <issue番号> --repo TomorrowsMealPlanningBoard/webapp --add-label "in-progress"
# GitHub Project側のステータス更新（可能な場合）
gh project item-edit --project-id 2 --id <item-id> --field-id <status-field-id> --single-select-option-id <in-progress-id>
```

---

## Phase 2: ブランチ作成・実装

### 2-1. ブランチを作成する

```bash
git checkout -b feature/issue-<番号>-<短い説明>
```

### 2-2. 実装する

webapp/CLAUDE.md のデザインルール・テックスタックに従う。

**実装時の原則（CLAUDE.mdより）：**
- 層1（アレルギー・禁止食材）は `if` 文による決定的フィルタのみ。ベクトル検索は使わない
- 設計判断に迷ったら `SPEC.md` を参照する
- Tailwind CSS + daisyUI を使う。カスタムCSSは書かない

---

## Phase 3: テスト

### 3-1. ユニットテストを実行する

```bash
cd /home/tutti/repos/gcloud-devops-aiagent-hackathon2/webapp
uv run pytest tests/unit/ -v
```

失敗した場合は修正してから次へ進む。

### 3-2. E2Eテストを実行する

```bash
docker compose up -d
uv run pytest tests/e2e/ -v
```

失敗した場合は修正してから次へ進む。

E2Eテストが全件パスしたら、実装した機能の主要な画面（初期表示・操作後の状態など、ACに対応する箇所）のスクリーンショットを Playwright で取得しておく。取得した画像は Phase 4 の PR body に貼り付ける。

### 3-3. ACの全項目を自己チェックする

チケットのACを1項目ずつ読み、実際に確認できたものにチェックを入れる。
**未確認の項目があればPRを作成しない。**

---

## Phase 4: PR作成

全テストがパスし、ACの全項目が確認できたらPRを作成する：

```bash
git push -u origin feature/issue-<番号>-<短い説明>

gh pr create \
  --repo TomorrowsMealPlanningBoard/webapp \
  --title "<変更の要約>" \
  --body "$(cat <<'EOF'
## 概要
<実装内容を1-2文で>

## Acceptance Criteria チェック
- [x] ...
- [x] ...

## テスト確認
- [x] `uv run pytest tests/unit/` 全件パス
- [x] E2Eで画面から操作して確認済み

## スクリーンショット
<!-- E2E確認時に取得した画面をここに貼り付ける -->

## 関連チケット
Closes #<issue番号>
EOF
)"
```

---

## エラーケース

**テストが通らない場合：** 修正してPhase 3に戻る。PRは作成しない。

**ACが確定できない場合：** ユーザーの判断を待つ。推測で実装しない。

**SPECとの重大な矛盾を発見した場合：** 実装を止めてユーザーに報告する。

---

## 人間が手元で確認するためのコマンド

各 worktree は独立した Docker 環境を持つが、`docker-compose.yml` のホストポートが `8000` にべた書きされているため、**同時に2つ以上は起動できない**。1つを確認したら必ず止めてから次を起動すること。

```bash
# 確認したい worktree に移動して起動
cd /home/tutti/repos/gcloud-devops-aiagent-hackathon2/worktree-issue-<番号>
docker compose up -d

# ブラウザで確認
# http://localhost:8000

# 確認が終わったら必ず止める（次の worktree を起動する前に）
docker compose down
```

複数 worktree を切り替えながら確認する場合の流れ：

```bash
# worktree A を確認
cd worktree-issue-XX && docker compose up -d
# → http://localhost:8000 で確認
docker compose down  # ← 必ず止める

# worktree B を確認
cd ../worktree-issue-YY && docker compose up -d
# → http://localhost:8000 で確認
docker compose down
```

> **注意:** `docker compose down` せずに別の worktree で `docker compose up` すると `port 8000 already in use` エラーになる。その場合は元の worktree に戻って `docker compose down` してから再試行する。

---

## チェックリスト（完了の定義）

- [ ] Phase 0: SPEC整合性チェックを実施し、ギャップをユーザーに報告した
- [ ] Phase 1: ACが確定し、チケットに記載されている
- [ ] Phase 1: ステータスをIn Progressに更新した
- [ ] Phase 2: 実装が完了した
- [ ] Phase 3: `uv run pytest tests/unit/` が全件パス
- [ ] Phase 3: E2Eで画面から操作して確認済み
- [ ] Phase 3: ACの全項目を自己チェック済み
- [ ] Phase 4: PRが作成され、チケットにリンクされている
