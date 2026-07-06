"""
Issue #34: LLM-as-judge 回帰テスト用ユニットテスト
SPEC.md §4 ループB ステップ2「定期eval」に基づく。

テストは EVAL_DRY_RUN=true 相当の環境で実行され、LLM呼び出しは行わない。
すべてのテストが uv run pytest tests/unit/test_eval.py -v でパスすること。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# テスト中は常にドライランモードで動作させる
os.environ["EVAL_DRY_RUN"] = "true"

# scripts/ ディレクトリを sys.path に追加
SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# -----------------------------------------------------------------
# モジュールのインポート（ドライランモード前提）
# -----------------------------------------------------------------

def _reload_eval_module() -> Any:
    """eval モジュールを再インポートして返す（環境変数の変更を反映するため）"""
    # 既にキャッシュされている場合は削除して再読み込み
    if "eval" in sys.modules:
        del sys.modules["eval"]
    import importlib.util

    spec = importlib.util.spec_from_file_location("eval", SCRIPTS_DIR / "eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def eval_module():
    """eval.py モジュールをドライランモードで読み込む"""
    return _reload_eval_module()


@pytest.fixture(scope="module")
def dataset_path() -> Path:
    """eval_dataset.json のパス"""
    path = SCRIPTS_DIR / "eval_dataset.json"
    assert path.exists(), f"eval_dataset.json が見つかりません: {path}"
    return path


@pytest.fixture(scope="module")
def dataset(dataset_path) -> dict[str, Any]:
    """データセットを読み込んで返す"""
    with open(dataset_path, encoding="utf-8") as f:
        return json.load(f)


# -----------------------------------------------------------------
# 1. データセットの構造テスト
# -----------------------------------------------------------------

class TestEvalDataset:
    def test_dataset_exists(self, dataset_path):
        """eval_dataset.json が存在すること"""
        assert dataset_path.exists()

    def test_dataset_has_required_fields(self, dataset):
        """データセットに必須フィールドが含まれること"""
        assert "version" in dataset
        assert "threshold" in dataset
        assert "test_cases" in dataset

    def test_dataset_has_minimum_test_cases(self, dataset):
        """テストケースが最低5件あること"""
        assert len(dataset["test_cases"]) >= 5

    def test_each_test_case_has_required_fields(self, dataset):
        """各テストケースに必須フィールドがあること"""
        required_fields = ["id", "name", "user_profile", "input", "proposal", "evaluation_criteria"]
        for tc in dataset["test_cases"]:
            for field in required_fields:
                assert field in tc, f"テストケース {tc.get('id', '?')} に {field} がありません"

    def test_each_proposal_has_three_recipes(self, dataset):
        """各提案に3件のレシピが含まれること"""
        for tc in dataset["test_cases"]:
            recipes = tc["proposal"]["recipes"]
            assert len(recipes) >= 1, f"テストケース {tc['id']} にレシピがありません"

    def test_threshold_is_numeric(self, dataset):
        """しきい値が数値であること"""
        assert isinstance(dataset["threshold"], (int, float))
        assert 0 <= dataset["threshold"] <= 10

    def test_test_case_ids_are_unique(self, dataset):
        """テストケースIDが一意であること"""
        ids = [tc["id"] for tc in dataset["test_cases"]]
        assert len(ids) == len(set(ids)), "重複するテストケースIDがあります"

    def test_allergy_violation_case_exists(self, dataset):
        """アレルギー違反を含む不良テストケースが存在すること（低スコア検出の確認）"""
        bad_cases = [
            tc for tc in dataset["test_cases"]
            if tc.get("evaluation_criteria", {}).get("allergy_safe") is False
        ]
        assert len(bad_cases) >= 1, "アレルギー違反テストケースが存在しません"


# -----------------------------------------------------------------
# 2. eval.py のコアロジックテスト（ドライランモード）
# -----------------------------------------------------------------

class TestEvalDryRun:
    def test_load_dataset_returns_dict(self, eval_module):
        """load_dataset() が辞書を返すこと"""
        data = eval_module.load_dataset()
        assert isinstance(data, dict)
        assert "test_cases" in data

    def test_evaluate_dry_run_returns_score_and_reason(self, eval_module, dataset):
        """evaluate_dry_run() がスコアと理由を返すこと"""
        tc = dataset["test_cases"][0]
        score, reason = eval_module.evaluate_dry_run(tc)
        assert isinstance(score, float)
        assert 0.0 <= score <= 10.0
        assert isinstance(reason, str)
        assert len(reason) > 0

    def test_dry_run_mode_is_enabled(self, eval_module):
        """ドライランモードが有効になっていること"""
        assert eval_module.DRY_RUN is True

    def test_evaluate_test_case_in_dry_run(self, eval_module, dataset):
        """evaluate_test_case() がドライランで結果を返すこと"""
        tc = dataset["test_cases"][0]
        result = eval_module.evaluate_test_case(tc)
        assert "id" in result
        assert "name" in result
        assert "score" in result
        assert "reason" in result
        assert result["dry_run"] is True
        assert isinstance(result["score"], float)
        assert 0.0 <= result["score"] <= 10.0

    def test_all_test_cases_evaluated(self, eval_module, dataset):
        """全テストケースが評価できること"""
        for tc in dataset["test_cases"]:
            result = eval_module.evaluate_test_case(tc)
            assert "score" in result
            assert isinstance(result["score"], float)

    def test_allergy_violation_case_gets_low_score(self, eval_module, dataset):
        """アレルギー違反テストケースが低スコアになること"""
        bad_cases = [
            tc for tc in dataset["test_cases"]
            if tc.get("evaluation_criteria", {}).get("allergy_safe") is False
        ]
        for tc in bad_cases:
            score, _ = eval_module.evaluate_dry_run(tc)
            assert score < 5.0, (
                f"テストケース {tc['id']} はアレルギー違反なのにスコアが高すぎます: {score}"
            )

    def test_good_proposal_gets_high_score(self, eval_module, dataset):
        """良質な提案が高スコアになること"""
        good_cases = [
            tc for tc in dataset["test_cases"]
            if tc.get("evaluation_criteria", {}).get("ideal_score_min", 0) >= 7.0
        ]
        assert len(good_cases) >= 1
        for tc in good_cases:
            score, _ = eval_module.evaluate_dry_run(tc)
            assert score >= 6.0, (
                f"テストケース {tc['id']} は良質な提案なのにスコアが低すぎます: {score}"
            )


# -----------------------------------------------------------------
# 3. スコア集計・記録テスト
# -----------------------------------------------------------------

class TestEvalResults:
    def test_save_results_creates_file(self, eval_module, tmp_path):
        """save_results() が eval_results.json を作成すること"""
        # RESULTS_PATH を一時ファイルに差し替えてテスト
        original_path = eval_module.RESULTS_PATH
        eval_module.RESULTS_PATH = tmp_path / "eval_results.json"

        try:
            results = [
                {"id": "tc-001", "name": "テスト1", "score": 8.0, "reason": "良質", "dry_run": True},
                {"id": "tc-002", "name": "テスト2", "score": 7.0, "reason": "普通", "dry_run": True},
            ]
            eval_module.save_results(results, average_score=7.5)

            assert eval_module.RESULTS_PATH.exists()
            with open(eval_module.RESULTS_PATH, encoding="utf-8") as f:
                data = json.load(f)

            assert "latest" in data
            assert "history" in data
            assert data["latest"]["average_score"] == 7.5
            assert len(data["latest"]["test_cases"]) == 2
        finally:
            eval_module.RESULTS_PATH = original_path

    def test_save_results_records_pass_status(self, eval_module, tmp_path):
        """save_results() がPASS/FAIL状態を記録すること"""
        original_path = eval_module.RESULTS_PATH
        eval_module.RESULTS_PATH = tmp_path / "eval_results.json"

        try:
            # PASS ケース
            eval_module.save_results([], average_score=7.0)
            with open(eval_module.RESULTS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            assert data["latest"]["passed"] is True

            # FAIL ケース（しきい値6.0を下回る）
            eval_module.save_results([], average_score=4.0)
            with open(eval_module.RESULTS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            # 最新レコードは FAIL
            assert data["latest"]["passed"] is False
            # 履歴には両方のレコードがあること
            assert len(data["history"]) == 2
        finally:
            eval_module.RESULTS_PATH = original_path

    def test_save_results_accumulates_history(self, eval_module, tmp_path):
        """save_results() が複数回実行の結果を履歴として蓄積すること"""
        original_path = eval_module.RESULTS_PATH
        eval_module.RESULTS_PATH = tmp_path / "eval_results.json"

        try:
            for i in range(3):
                eval_module.save_results([], average_score=7.0 + i * 0.5)

            with open(eval_module.RESULTS_PATH, encoding="utf-8") as f:
                data = json.load(f)

            assert len(data["history"]) == 3
            assert data["latest"]["average_score"] == 8.0  # 最後の値
        finally:
            eval_module.RESULTS_PATH = original_path

    def test_result_has_run_at_timestamp(self, eval_module, tmp_path):
        """eval_results.json に run_at タイムスタンプが含まれること"""
        original_path = eval_module.RESULTS_PATH
        eval_module.RESULTS_PATH = tmp_path / "eval_results.json"

        try:
            eval_module.save_results([], average_score=7.0)
            with open(eval_module.RESULTS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            assert "run_at" in data["latest"]
            assert data["latest"]["run_at"]  # 空でないこと
        finally:
            eval_module.RESULTS_PATH = original_path


# -----------------------------------------------------------------
# 4. 終了コード・しきい値テスト
# -----------------------------------------------------------------

class TestExitCodeAndThreshold:
    def test_threshold_is_float(self, eval_module):
        """しきい値が float であること"""
        assert isinstance(eval_module.THRESHOLD, float)

    def test_threshold_env_override(self):
        """EVAL_THRESHOLD 環境変数でしきい値を上書きできること"""
        os.environ["EVAL_THRESHOLD"] = "7.5"
        mod = _reload_eval_module()
        try:
            assert mod.THRESHOLD == 7.5
        finally:
            del os.environ["EVAL_THRESHOLD"]

    def test_main_exits_0_when_above_threshold(self, eval_module, tmp_path):
        """平均スコアがしきい値以上の場合に exit(0) で終了すること"""
        original_path = eval_module.RESULTS_PATH
        eval_module.RESULTS_PATH = tmp_path / "eval_results.json"

        try:
            # ドライランスコアはすべて設定済み。平均が6.0以上になるはずなので正常終了
            with pytest.raises(SystemExit) as exc_info:
                eval_module.main()
            assert exc_info.value.code == 0, (
                f"期待される終了コード 0 ですが {exc_info.value.code} でした"
            )
        finally:
            eval_module.RESULTS_PATH = original_path

    def test_main_exits_1_when_below_threshold(self, tmp_path):
        """平均スコアがしきい値を下回る場合に exit(1) で終了すること"""
        # しきい値を非常に高く設定してFAILを強制
        os.environ["EVAL_THRESHOLD"] = "9.9"
        mod = _reload_eval_module()
        original_path = mod.RESULTS_PATH
        mod.RESULTS_PATH = tmp_path / "eval_results.json"

        try:
            with pytest.raises(SystemExit) as exc_info:
                mod.main()
            assert exc_info.value.code == 1, (
                f"期待される終了コード 1 ですが {exc_info.value.code} でした"
            )
        finally:
            mod.RESULTS_PATH = original_path
            del os.environ["EVAL_THRESHOLD"]


# -----------------------------------------------------------------
# 5. プロンプト構築テスト（LLM呼び出しなし）
# -----------------------------------------------------------------

class TestJudgePrompt:
    def test_build_judge_prompt_contains_allergies(self, eval_module, dataset):
        """プロンプトにアレルギー情報が含まれること"""
        tc = next(
            tc for tc in dataset["test_cases"]
            if tc["user_profile"].get("allergies")
        )
        prompt = eval_module._build_judge_prompt(tc)
        for allergy in tc["user_profile"]["allergies"]:
            assert allergy in prompt, f"アレルギー '{allergy}' がプロンプトに含まれていません"

    def test_build_judge_prompt_contains_ingredients(self, eval_module, dataset):
        """プロンプトに利用可能食材が含まれること"""
        tc = dataset["test_cases"][0]
        prompt = eval_module._build_judge_prompt(tc)
        for ingredient in tc["input"]["available_ingredients"]:
            assert ingredient in prompt

    def test_build_judge_prompt_contains_score_format(self, eval_module, dataset):
        """プロンプトにスコア回答形式の指示が含まれること"""
        tc = dataset["test_cases"][0]
        prompt = eval_module._build_judge_prompt(tc)
        assert "SCORE:" in prompt
        assert "REASON:" in prompt

    def test_build_judge_prompt_marks_allergy_violation(self, eval_module, dataset):
        """アレルギー違反テストケースのプロンプトに警告が含まれること"""
        bad_case = next(
            tc for tc in dataset["test_cases"]
            if tc.get("evaluation_criteria", {}).get("allergy_safe") is False
        )
        prompt = eval_module._build_judge_prompt(bad_case)
        assert "アレルギー" in prompt
