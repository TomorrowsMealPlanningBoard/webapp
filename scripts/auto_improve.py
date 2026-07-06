#!/usr/bin/env python3
"""
auto_improve.py — スコア低下時の改善PR自動起票スクリプト

SPEC.md §4 ループB「3. 自動起票」を実装する。
eval_results.json を読み取り、スコアが閾値を下回った場合に:
  1. Gemini でプロンプト改善案を生成
  2. prompts/ ファイルを修正したブランチを作成
  3. GitHub に Draft PR を作成（自動マージなし / Human-in-the-loop）
  4. Slack Webhook に通知を送信

環境変数:
  DRY_RUN=true          LLM呼び出しをスキップし、固定の修正案テキストを使用する
  GOOGLE_CLOUD_PROJECT  Vertex AI プロジェクト ID（DRY_RUN=false 時に必要）
  SLACK_WEBHOOK_URL     Slack Webhook URL（未設定時はスキップ）
  GITHUB_TOKEN          gh コマンドの認証トークン（GitHub Actions で自動設定）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent

EVAL_RESULTS_PATH = PROJECT_ROOT / "scripts" / "eval_results.json"
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# Gemini モデル（コスト効率最優先）
GEMINI_MODEL = "gemini-2.0-flash-lite"


# ---------------------------------------------------------------------------
# eval_results.json の読み込み
# ---------------------------------------------------------------------------

def load_eval_results(path: Path = EVAL_RESULTS_PATH) -> dict:
    """eval_results.json を読み込んで返す。"""
    if not path.exists():
        raise FileNotFoundError(f"eval_results.json が見つかりません: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def should_trigger(results: dict) -> bool:
    """スコアが閾値を下回っている場合（passed=false）に True を返す。"""
    return not results.get("passed", True)


# ---------------------------------------------------------------------------
# Gemini による改善案生成
# ---------------------------------------------------------------------------

def _build_analysis_prompt(results: dict, prompt_contents: dict[str, str]) -> str:
    """改善案生成用のプロンプト文字列を組み立てる。"""
    low_scores = [
        s for s in results.get("scores", [])
        if s.get("score", 10) < results.get("threshold", 6.0)
    ]
    low_score_text = "\n".join(
        f"- {s['test_case_id']}: スコア {s['score']} — {s.get('reason', '理由なし')}"
        for s in low_scores
    )

    prompts_text = "\n\n".join(
        f"### {name}.md\n```\n{content}\n```"
        for name, content in prompt_contents.items()
    )

    return f"""あなたは献立提案AIシステムのプロンプトエンジニアです。
以下のevalスコアレポートを分析し、プロンプトの改善案を提案してください。

## evalスコアレポート
- 平均スコア: {results.get('average_score', 'N/A')}
- 閾値: {results.get('threshold', 6.0)}
- タイムスタンプ: {results.get('timestamp', 'N/A')}

## 低スコアのテストケース
{low_score_text}

## 現在のプロンプトファイル
{prompts_text}

## 指示
1. スコアが低下した原因を分析してください（3点以内で箇条書き）
2. `prompts/suggest.md` の `<!-- PROMPT:START -->` と `<!-- PROMPT:END -->` の間にある
   プロンプト本文の改善案を提案してください
3. 改善後のプロンプト本文を完全な形で出力してください

## 出力フォーマット（JSONのみ。説明文は不要）
{{
  "cause_summary": "スコア低下の原因サマリー（1-2文）",
  "causes": ["原因1", "原因2", "原因3"],
  "improved_suggest_prompt": "改善後のプロンプト本文（完全な文字列）"
}}
"""


def _dry_run_improvement(results: dict) -> dict:
    """DRY_RUN=true 時に返す固定の改善案。"""
    return {
        "cause_summary": "[DRY RUN] スコア低下の原因: 制約ルールの優先度が不明確なため、"
                         "バリエーション確保と食材活用のバランスが取れていない。",
        "causes": [
            "[DRY RUN] アレルギー除外ルールの記述が曖昧で見落としリスクがある",
            "[DRY RUN] 3候補のバリエーション指示が弱い",
            "[DRY RUN] 食品ロス削減の優先度が明示されていない",
        ],
        "improved_suggest_prompt": (
            "あなたは家庭料理の献立提案AIです。\n"
            "ユーザーの情報をもとに、**今回の食事（1回分）**に合う**3つの候補レシピ**を提案してください。\n"
            "ユーザーはこの3候補の中から気に入ったものを1つ選びます。\n\n"
            "## ユーザー情報\n\n"
            "### プロファイル\n"
            "- アレルギー食材（絶対に使用禁止 — 材料・調味料・隠し味すべてを含む）: {allergies}\n"
            "- 禁止・苦手食材（除外すること）: {forbidden_ingredients}\n"
            "- 利用可能な調理器具: {kitchen_tools}\n"
            "- 食事の目標: {goal}\n\n"
            "### 冷蔵庫の食材（できるだけ使ってください）\n"
            "{ingredients_list}\n\n"
            "### 今日の条件\n"
            "- 調理時間の上限: {cooking_time_label}\n"
            "- 手間レベル: {effort_label}\n"
            "- 気分・食べたいもの: {mood_description}\n\n"
            "### 過去のフィードバック（参考情報）\n"
            "- 除外したいもの（不採用タグ）: {negative_tags}\n"
            "- 好みのもの（ポジティブタグ）: {positive_tags}\n\n"
            "### 直近7日以内に提案したレシピ（重複回避のため避けること）\n"
            "{recent_proposal_titles}\n\n"
            "## 制約ルール（優先度順に必ず守ること）\n\n"
            "1. 【最優先】**アレルギー食材は絶対に使用しない**。"
            "材料・調味料・隠し味・出汁すべてを対象とする。\n"
            "2. **禁止・苦手食材も使用しない**。\n"
            "3. **除外タグに含まれる食材・味付けの料理は避ける**。\n"
            "4. **利用可能な調理器具のみを使用する**。"
            "器具が空の場合は包丁・フライパン・鍋のみを前提とする。\n"
            "5. 3つの候補は互いに**バリエーションを持たせる**"
            "（主食材・調理法・味付けがすべて異なること）。\n"
            "6. 【食品ロス削減優先】冷蔵庫の食材を積極的に活用し、"
            "賞味期限の近い食材から先に使う候補を優先する。\n"
            "7. **直近7日以内に提案したレシピと同じ・類似のレシピは避ける**（重複回避）。\n"
        ),
    }


def generate_improvement(results: dict) -> dict:
    """
    Gemini を使ってプロンプト改善案を生成する。
    DRY_RUN=true の場合は固定の改善案を返す。
    """
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    if dry_run:
        print("[DRY RUN] LLM呼び出しをスキップします。固定の改善案を使用します。")
        return _dry_run_improvement(results)

    # prompts/ ファイルの内容を読み込む
    prompt_contents: dict[str, str] = {}
    for md_file in sorted(PROMPTS_DIR.glob("*.md")):
        prompt_contents[md_file.stem] = md_file.read_text(encoding="utf-8")

    analysis_prompt = _build_analysis_prompt(results, prompt_contents)

    # Vertex AI Gemini 呼び出し
    try:
        import google.genai as genai

        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise EnvironmentError("GOOGLE_CLOUD_PROJECT 環境変数が設定されていません")

        client = genai.Client(vertexai=True, project=project, location="us-central1")
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=analysis_prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        text = response.text.strip()
        # JSON コードブロックを除去
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Gemini API 呼び出しに失敗しました: {exc}") from exc


# ---------------------------------------------------------------------------
# prompts/ ファイルの更新
# ---------------------------------------------------------------------------

def _replace_prompt_body(md_content: str, new_body: str) -> str:
    """Markdownファイルの PROMPT:START〜END の間の本文を new_body に置き換える。"""
    start_marker = "<!-- PROMPT:START -->"
    end_marker = "<!-- PROMPT:END -->"
    start_idx = md_content.find(start_marker)
    end_idx = md_content.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        return md_content
    return (
        md_content[: start_idx + len(start_marker)]
        + "\n"
        + new_body.strip()
        + "\n"
        + md_content[end_idx:]
    )


def apply_improvement(improvement: dict) -> list[Path]:
    """
    改善案を prompts/suggest.md に適用して保存する。
    変更したファイルのリストを返す。
    """
    suggest_path = PROMPTS_DIR / "suggest.md"
    if not suggest_path.exists():
        print(f"[WARN] {suggest_path} が見つかりません。スキップします。")
        return []

    original = suggest_path.read_text(encoding="utf-8")
    improved_body = improvement.get("improved_suggest_prompt", "")
    if not improved_body:
        print("[WARN] 改善案が空のためファイル更新をスキップします。")
        return []

    updated = _replace_prompt_body(original, improved_body)
    if updated == original:
        print("[INFO] 変更差分なし。ファイル更新をスキップします。")
        return []

    suggest_path.write_text(updated, encoding="utf-8")
    print(f"[INFO] {suggest_path} を更新しました。")
    return [suggest_path]


# ---------------------------------------------------------------------------
# Git ブランチ作成・PR 起票
# ---------------------------------------------------------------------------

def _run(
    args: list[str], cwd: Path = PROJECT_ROOT, check: bool = True
) -> subprocess.CompletedProcess:
    """サブプロセスを実行し、結果を返す。"""
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check)


def create_branch_and_pr(
    results: dict,
    improvement: dict,
    changed_files: list[Path],
) -> str | None:
    """
    変更ブランチを作成し、GitHub に Draft PR を起票する。
    作成した PR の URL を返す（gh コマンドが使えない場合は None）。
    """
    if not changed_files:
        print("[INFO] 変更ファイルがないため PR 作成をスキップします。")
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    branch_name = f"auto-improve/{timestamp}"

    # ブランチ作成
    _run(["git", "checkout", "-b", branch_name])
    print(f"[INFO] ブランチを作成しました: {branch_name}")

    # 変更をステージング・コミット
    for f in changed_files:
        _run(["git", "add", str(f)])

    commit_msg = (
        f"fix(prompts): auto-improve prompts based on eval score drop\n\n"
        f"Average score: {results.get('average_score', 'N/A')} "
        f"(threshold: {results.get('threshold', 6.0)})\n"
        f"Cause: {improvement.get('cause_summary', '')}"
    )
    _run(["git", "commit", "-m", commit_msg])
    print("[INFO] 変更をコミットしました。")

    # プッシュ
    _run(["git", "push", "-u", "origin", branch_name])
    print(f"[INFO] ブランチをプッシュしました: {branch_name}")

    # PR 本文を組み立てる
    causes_text = "\n".join(f"- {c}" for c in improvement.get("causes", []))
    low_scores = [
        s for s in results.get("scores", [])
        if s.get("score", 10) < results.get("threshold", 6.0)
    ]
    low_scores_text = "\n".join(
        f"| {s['test_case_id']} | {s['score']} | {s.get('reason', '')} |"
        for s in low_scores
    )

    pr_body = f"""## 概要

evalスコアが閾値を下回ったため、自動改善PRを起票しました。

**このPRは自動マージされません。** 必ず人間のレビューを経てからマージしてください。

## スコアサマリー

| 項目 | 値 |
|------|-----|
| 平均スコア | {results.get('average_score', 'N/A')} |
| 閾値 | {results.get('threshold', 6.0)} |
| タイムスタンプ | {results.get('timestamp', 'N/A')} |

## 低スコアのテストケース

| テストケースID | スコア | 理由 |
|--------------|-------|------|
{low_scores_text}

## 原因の要約

{improvement.get('cause_summary', '')}

### 詳細

{causes_text}

## 変更内容

- `prompts/suggest.md` のプロンプト本文を改善
  - 制約ルールの優先度を明確化
  - アレルギー除外ルールをより厳格に記述
  - バリエーション指示を強化

## レビューのポイント

- 改善後のプロンプトが意図したとおりに動作するか確認してください
- 既存のACに定義されたユースケースがすべてカバーされているか確認してください
- 必要に応じて `uv run pytest tests/unit/` を実行してテストが通ることを確認してください

## 関連

- 自動生成: eval スコア低下トリガー (SPEC.md §4 ループB)
"""

    try:
        result = _run([
            "gh", "pr", "create",
            "--repo", "TomorrowsMealPlanningBoard/webapp",
            "--title", f"fix(prompts): auto-improve based on eval score drop [{timestamp}]",
            "--body", pr_body,
            "--draft",
            "--base", "main",
        ])
        pr_url = result.stdout.strip()
        print(f"[INFO] Draft PR を作成しました: {pr_url}")
        return pr_url
    except subprocess.CalledProcessError as exc:
        print(f"[ERROR] gh pr create に失敗しました: {exc.stderr}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Slack 通知
# ---------------------------------------------------------------------------

def notify_slack(results: dict, improvement: dict, pr_url: str | None) -> None:
    """
    Slack Webhook に通知を送信する。
    SLACK_WEBHOOK_URL が未設定の場合はスキップする（エラーにしない）。
    """
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print("[INFO] SLACK_WEBHOOK_URL が未設定のため Slack 通知をスキップします。")
        return

    pr_text = f"<{pr_url}|PR を確認する>" if pr_url else "（PR URL 取得失敗）"
    message = {
        "text": (
            f":warning: *TomorrowsMeal: 品質低下を検知しました*\n"
            f"evalスコア *{results.get('average_score', 'N/A')}* が "
            f"閾値 *{results.get('threshold', 6.0)}* を下回りました。\n"
            f"原因: {improvement.get('cause_summary', '')}\n"
            f"改善PRを自動起票しました。{pr_text}\n"
            f"> *このPRは自動マージされません。* 人間のレビューをお願いします。"
        )
    }

    data = json.dumps(message).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print("[INFO] Slack に通知しました。")
            else:
                print(f"[WARN] Slack 通知のレスポンスが予期しないステータスです: {resp.status}")
    except Exception as exc:
        print(f"[WARN] Slack 通知に失敗しました（無視します）: {exc}")


# ---------------------------------------------------------------------------
# メインエントリポイント
# ---------------------------------------------------------------------------

def main(eval_results_path: Path = EVAL_RESULTS_PATH) -> int:
    """
    メイン処理。
    戻り値: 0=成功, 1=エラー
    """
    print("=== auto_improve.py 開始 ===")

    # eval_results.json を読み込む
    try:
        results = load_eval_results(eval_results_path)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    print(f"[INFO] average_score={results.get('average_score')}, "
          f"threshold={results.get('threshold')}, passed={results.get('passed')}")

    # 閾値チェック
    if not should_trigger(results):
        print("[INFO] スコアは閾値を上回っています。改善PRの起票は不要です。")
        return 0

    print("[INFO] スコアが閾値を下回っています。改善案を生成します。")

    # 改善案生成
    try:
        improvement = generate_improvement(results)
    except Exception as exc:
        print(f"[ERROR] 改善案の生成に失敗しました: {exc}", file=sys.stderr)
        return 1

    print(f"[INFO] 原因サマリー: {improvement.get('cause_summary', '')}")

    # prompts/ ファイルへの適用
    changed_files = apply_improvement(improvement)

    # ブランチ作成・PR 起票
    pr_url = create_branch_and_pr(results, improvement, changed_files)

    # Slack 通知
    notify_slack(results, improvement, pr_url)

    print("=== auto_improve.py 完了 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
