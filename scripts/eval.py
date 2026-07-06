"""
eval.py — LLM-as-judge による提案品質評価スクリプト (SPEC.md §4 ループB ステップ2)

使い方:
  uv run python scripts/eval.py                    # LLM評価（GOOGLE_CLOUD_PROJECT必要）
  EVAL_DRY_RUN=true uv run python scripts/eval.py  # ドライラン（LLM呼び出しなし・CI用）

終了コード:
  0 = 全テストケースのスコア平均がしきい値以上
  1 = スコアがしきい値を下回った（品質低下の検知）
  2 = 設定エラー・実行エラー

スコアは scripts/eval_results.json に記録される（回帰比較用）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ドライランモード: EVAL_DRY_RUN=true でLLM呼び出しをスキップして固定スコアを返す
DRY_RUN = os.getenv("EVAL_DRY_RUN", "false").lower() in ("true", "1", "yes")

# しきい値（データセットの設定を使うが、環境変数で上書き可能）
DEFAULT_THRESHOLD = 6.0
THRESHOLD = float(os.getenv("EVAL_THRESHOLD", str(DEFAULT_THRESHOLD)))

# デフォルトモデル
DEFAULT_MODEL = "gemini-3.1-flash-lite"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)

# パス設定
SCRIPT_DIR = Path(__file__).parent
DATASET_PATH = SCRIPT_DIR / "eval_dataset.json"
RESULTS_PATH = SCRIPT_DIR / "eval_results.json"

# ドライランで返す固定スコアのマッピング（テストケースIDごと）
_DRY_RUN_SCORES: dict[str, float] = {
    "tc-001": 8.0,  # 良質な提案 → 高スコア
    "tc-002": 7.5,  # 良質な提案 → 高スコア
    "tc-003": 2.0,  # アレルギー違反 → 低スコア
    "tc-004": 5.5,  # 時間超過 → 中スコア
    "tc-005": 9.0,  # 嗜好プロファイル完全遵守 → 最高スコア
}

_DRY_RUN_REASONS: dict[str, str] = {
    "tc-001": "[DRY_RUN] アレルギー配慮・食材活用・時間内・ムード適合すべて満たす良質な提案",
    "tc-002": "[DRY_RUN] 時短要件を満たし利用可能食材を活用した朝食提案",
    "tc-003": "[DRY_RUN] アレルギー食材(えび)が全レシピに含まれており安全性基準を満たさない",
    "tc-004": "[DRY_RUN] 大半のレシピが調理時間制限(20分)を超過している",
    "tc-005": "[DRY_RUN] 過去の嗜好タグ・アレルギー・ムードすべてに完璧に対応した高品質提案",
}


def load_dataset() -> dict[str, Any]:
    """評価データセットを読み込む"""
    if not DATASET_PATH.exists():
        print(f"[ERROR] データセットが見つかりません: {DATASET_PATH}", file=sys.stderr)
        sys.exit(2)
    with open(DATASET_PATH, encoding="utf-8") as f:
        return json.load(f)


def _build_judge_prompt(test_case: dict[str, Any]) -> str:
    """LLM-as-judge 用のプロンプトを構築する"""
    user_profile = test_case["user_profile"]
    input_data = test_case["input"]
    proposal = test_case["proposal"]
    criteria = test_case["evaluation_criteria"]

    allergies_str = "、".join(user_profile.get("allergies", [])) or "なし"
    disliked_str = "、".join(user_profile.get("disliked_ingredients", [])) or "なし"
    ingredients_str = "、".join(input_data.get("available_ingredients", []))
    recipes_str = json.dumps(proposal["recipes"], ensure_ascii=False, indent=2)

    # 評価基準をプロンプトに含める
    criteria_notes = []
    if criteria.get("allergy_safe") is False:
        criteria_notes.append("※ この提案はアレルギー食材を含む不良例です（低スコアが期待されます）")
    if criteria.get("within_time_limit") is False:
        criteria_notes.append("※ この提案は調理時間制限を超過しています")
    criteria_str = "\n".join(criteria_notes) if criteria_notes else ""

    return f"""あなたは料理提案の品質を評価する専門家（LLM-as-judge）です。
以下の献立提案を0〜10点で評価し、理由とともに回答してください。

## ユーザープロフィール
- アレルギー: {allergies_str}
- 嫌いな食材: {disliked_str}
- 調理器具: {", ".join(user_profile.get("cooking_equipment", []))}
- 食の方向性: {user_profile.get("meal_direction", "指定なし")}

## 今回の条件
- 利用可能食材: {ingredients_str}
- 調理時間上限: {input_data.get("cooking_time_minutes")}分
- 気分: {input_data.get("mood", "指定なし")}

## 提案されたレシピ
{recipes_str}

{criteria_str}

## 評価基準
1. アレルギー安全性（最重要）: アレルギー食材が含まれていないか（含まれる場合は大幅減点）
2. 食材活用度: 利用可能な食材をうまく活用しているか
3. 時間適合性: 調理時間制限内に収まっているか
4. ムード適合性: ユーザーの気分に合った提案か
5. バリエーション: 3案に適切な多様性があるか

## 回答形式（必ずこの形式で回答してください）
SCORE: <0から10の数値（小数点1桁まで可）>
REASON: <評価理由を1〜3文で>
"""


def evaluate_with_llm(test_case: dict[str, Any]) -> tuple[float, str]:
    """Gemini LLM を使って提案品質を評価する（0〜10点）"""
    import os

    from google import genai

    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        print("[ERROR] GOOGLE_CLOUD_PROJECT 環境変数が設定されていません", file=sys.stderr)
        sys.exit(2)

    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    client = genai.Client(vertexai=True, project=project, location=location)

    prompt = _build_judge_prompt(test_case)

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        response_text = response.text.strip()
    except Exception as e:
        print(f"[ERROR] LLM呼び出しに失敗しました: {e}", file=sys.stderr)
        sys.exit(2)

    # レスポンスをパース
    score = None
    reason = ""
    for line in response_text.splitlines():
        if line.startswith("SCORE:"):
            try:
                score = float(line.split(":", 1)[1].strip())
                score = max(0.0, min(10.0, score))  # 0〜10にクランプ
            except ValueError:
                pass
        elif line.startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()

    if score is None:
        # パース失敗時はフォールバック（中央値）
        print(
            f"[WARN] テストケース {test_case['id']} のスコアパースに失敗。デフォルト5.0を使用。",
            file=sys.stderr,
        )
        score = 5.0
        reason = f"(パース失敗) LLMレスポンス: {response_text[:200]}"

    return score, reason


def evaluate_dry_run(test_case: dict[str, Any]) -> tuple[float, str]:
    """ドライランモード: LLM呼び出しなしで固定スコアを返す"""
    tc_id = test_case["id"]
    score = _DRY_RUN_SCORES.get(tc_id, 6.5)
    reason = _DRY_RUN_REASONS.get(tc_id, f"[DRY_RUN] テストケース {tc_id} の固定スコア")
    return score, reason


def evaluate_test_case(test_case: dict[str, Any]) -> dict[str, Any]:
    """1つのテストケースを評価して結果を返す"""
    tc_id = test_case["id"]
    tc_name = test_case["name"]

    print(f"\n評価中: [{tc_id}] {tc_name}")

    if DRY_RUN:
        score, reason = evaluate_dry_run(test_case)
        print(f"  → スコア: {score:.1f}/10 (ドライラン)")
    else:
        score, reason = evaluate_with_llm(test_case)
        print(f"  → スコア: {score:.1f}/10")

    print(f"     理由: {reason}")

    # 期待スコアとの比較ログ
    criteria = test_case.get("evaluation_criteria", {})
    if "ideal_score_min" in criteria and score < criteria["ideal_score_min"]:
        print(f"  [WARN] 期待最小スコア({criteria['ideal_score_min']})を下回っています")
    if "ideal_score_max" in criteria and score > criteria["ideal_score_max"]:
        print(f"  [WARN] 期待最大スコア({criteria['ideal_score_max']})を上回っています")

    return {
        "id": tc_id,
        "name": tc_name,
        "score": score,
        "reason": reason,
        "dry_run": DRY_RUN,
    }


def save_results(results: list[dict[str, Any]], average_score: float) -> None:
    """評価結果を eval_results.json に記録する（回帰比較用）"""
    # 既存の結果を読み込んで履歴として保持
    history: list[dict[str, Any]] = []
    if RESULTS_PATH.exists():
        try:
            with open(RESULTS_PATH, encoding="utf-8") as f:
                existing = json.load(f)
                history = existing.get("history", [])
        except (json.JSONDecodeError, KeyError):
            history = []

    # 新しい実行記録を追加
    run_record = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": DRY_RUN,
        "model": GEMINI_MODEL if not DRY_RUN else "dry_run",
        "threshold": THRESHOLD,
        "average_score": average_score,
        "passed": average_score >= THRESHOLD,
        "test_cases": results,
    }
    history.append(run_record)

    output = {
        "latest": run_record,
        "history": history,
    }

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n結果を保存しました: {RESULTS_PATH}")


def main() -> None:
    print("=" * 60)
    print("TomorrowsMeal LLM-as-judge 提案品質評価")
    print("=" * 60)
    if DRY_RUN:
        print("[モード] ドライラン（LLM呼び出しなし・固定スコア使用）")
    else:
        print(f"[モード] LLM評価（モデル: {GEMINI_MODEL}）")
    print(f"[しきい値] {THRESHOLD}")

    dataset = load_dataset()
    test_cases = dataset.get("test_cases", [])

    if not test_cases:
        print("[ERROR] テストケースが0件です", file=sys.stderr)
        sys.exit(2)

    print(f"\nテストケース数: {len(test_cases)}")

    # 全テストケースを評価
    results: list[dict[str, Any]] = []
    for tc in test_cases:
        result = evaluate_test_case(tc)
        results.append(result)

    # 集計
    scores = [r["score"] for r in results]
    average_score = sum(scores) / len(scores)

    print("\n" + "=" * 60)
    print("評価結果サマリー")
    print("=" * 60)
    for r in results:
        status = "PASS" if r["score"] >= THRESHOLD else "FAIL"
        print(f"  [{status}] [{r['id']}] {r['name']}: {r['score']:.1f}/10")
    print(f"\n平均スコア: {average_score:.2f}/10")
    print(f"しきい値  : {THRESHOLD}/10")

    # 結果の保存
    save_results(results, average_score)

    # 終了コードの判定
    if average_score >= THRESHOLD:
        print(f"\n[PASS] 品質基準を満たしています（{average_score:.2f} >= {THRESHOLD}）")
        sys.exit(0)
    else:
        print(
            f"\n[FAIL] 品質基準を下回っています（{average_score:.2f} < {THRESHOLD}）",
            file=sys.stderr,
        )
        print("[FAIL] 改善が必要です。eval_results.json を確認してください。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
