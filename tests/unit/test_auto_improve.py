"""
Issue #35: auto_improve.py のユニットテスト。

スコア低下時の改善PR自動起票スクリプト（SPEC.md §4 ループB）の各関数を
モック環境で検証する。Slack送信・gh コマンドはモックする。
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# テスト対象モジュールのインポート
from scripts.auto_improve import (
    _dry_run_improvement,
    _replace_prompt_body,
    apply_improvement,
    create_branch_and_pr,
    generate_improvement,
    load_eval_results,
    notify_slack,
    should_trigger,
)


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture
def passing_results() -> dict:
    """閾値を上回るevalスコア（passed=true）"""
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "scores": [
            {"test_case_id": "tc-001", "score": 8.5, "reason": "良好"},
            {"test_case_id": "tc-002", "score": 7.0, "reason": "良好"},
        ],
        "average_score": 7.75,
        "threshold": 6.0,
        "passed": True,
    }


@pytest.fixture
def failing_results() -> dict:
    """閾値を下回るevalスコア（passed=false）"""
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "scores": [
            {"test_case_id": "tc-001", "score": 4.0, "reason": "アレルギー除外が不徹底"},
            {"test_case_id": "tc-002", "score": 3.5, "reason": "バリエーション不足"},
        ],
        "average_score": 3.75,
        "threshold": 6.0,
        "passed": False,
    }


@pytest.fixture
def dry_run_improvement() -> dict:
    """DRY_RUN時の固定改善案"""
    return {
        "cause_summary": "[DRY RUN] スコア低下の原因サマリー",
        "causes": [
            "[DRY RUN] 原因1",
            "[DRY RUN] 原因2",
            "[DRY RUN] 原因3",
        ],
        "improved_suggest_prompt": "改善後のプロンプト本文",
    }


# ---------------------------------------------------------------------------
# load_eval_results のテスト
# ---------------------------------------------------------------------------

def test_load_eval_results_success(tmp_path: Path):
    """正常な eval_results.json を読み込めること"""
    eval_file = tmp_path / "eval_results.json"
    data = {
        "timestamp": "2026-01-01T00:00:00Z",
        "scores": [],
        "average_score": 5.0,
        "threshold": 6.0,
        "passed": False,
    }
    eval_file.write_text(json.dumps(data), encoding="utf-8")

    result = load_eval_results(eval_file)
    assert result["average_score"] == 5.0
    assert result["passed"] is False


def test_load_eval_results_file_not_found(tmp_path: Path):
    """存在しないファイルは FileNotFoundError を送出すること"""
    with pytest.raises(FileNotFoundError):
        load_eval_results(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# should_trigger のテスト
# ---------------------------------------------------------------------------

def test_should_trigger_when_failed(failing_results: dict):
    """passed=false の場合に True を返すこと"""
    assert should_trigger(failing_results) is True


def test_should_trigger_when_passed(passing_results: dict):
    """passed=true の場合に False を返すこと"""
    assert should_trigger(passing_results) is False


def test_should_trigger_default_no_passed_key():
    """passed キーが存在しない場合は False を返すこと（デフォルト True なのでトリガーしない）"""
    assert should_trigger({}) is False


# ---------------------------------------------------------------------------
# generate_improvement のテスト
# ---------------------------------------------------------------------------

def test_generate_improvement_dry_run(failing_results: dict, monkeypatch):
    """DRY_RUN=true の場合は LLM を呼ばず固定の改善案を返すこと"""
    monkeypatch.setenv("DRY_RUN", "true")
    result = generate_improvement(failing_results)

    assert "cause_summary" in result
    assert "causes" in result
    assert isinstance(result["causes"], list)
    assert len(result["causes"]) > 0
    assert "improved_suggest_prompt" in result
    assert "[DRY RUN]" in result["cause_summary"]


def test_generate_improvement_dry_run_false_raises_without_project(
    failing_results: dict, monkeypatch
):
    """DRY_RUN=false で GOOGLE_CLOUD_PROJECT が未設定の場合は RuntimeError を送出すること"""
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    with pytest.raises(RuntimeError, match="Gemini API 呼び出しに失敗しました"):
        generate_improvement(failing_results)


def test_dry_run_improvement_structure(failing_results: dict):
    """_dry_run_improvement が期待する構造を返すこと"""
    result = _dry_run_improvement(failing_results)
    assert isinstance(result, dict)
    assert "cause_summary" in result
    assert "causes" in result
    assert "improved_suggest_prompt" in result
    assert isinstance(result["causes"], list)
    assert isinstance(result["improved_suggest_prompt"], str)


# ---------------------------------------------------------------------------
# _replace_prompt_body のテスト
# ---------------------------------------------------------------------------

def test_replace_prompt_body_with_markers():
    """PROMPT:START〜END マーカーがある場合に本文を置き換えること"""
    original = """# タイトル

説明文

<!-- PROMPT:START -->
古いプロンプト本文
<!-- PROMPT:END -->

フッター
"""
    new_body = "新しいプロンプト本文"
    result = _replace_prompt_body(original, new_body)
    assert "新しいプロンプト本文" in result
    assert "古いプロンプト本文" not in result
    assert "<!-- PROMPT:START -->" in result
    assert "<!-- PROMPT:END -->" in result


def test_replace_prompt_body_without_markers():
    """マーカーがない場合はそのままのコンテンツを返すこと"""
    original = "マーカーのないコンテンツ"
    result = _replace_prompt_body(original, "新しい本文")
    assert result == original


def test_replace_prompt_body_preserves_header_footer():
    """ヘッダーとフッターが保持されること"""
    original = "ヘッダー\n<!-- PROMPT:START -->\n古い本文\n<!-- PROMPT:END -->\nフッター"
    result = _replace_prompt_body(original, "新しい本文")
    assert result.startswith("ヘッダー")
    assert "フッター" in result


# ---------------------------------------------------------------------------
# apply_improvement のテスト
# ---------------------------------------------------------------------------

def test_apply_improvement_updates_suggest_md(tmp_path: Path, monkeypatch):
    """改善案を prompts/suggest.md に適用できること"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    suggest_md = prompts_dir / "suggest.md"
    original_content = (
        "# タイトル\n\n"
        "<!-- PROMPT:START -->\n"
        "古いプロンプト\n"
        "<!-- PROMPT:END -->\n"
    )
    suggest_md.write_text(original_content, encoding="utf-8")

    # PROMPTS_DIR をモックする
    import scripts.auto_improve as module
    monkeypatch.setattr(module, "PROMPTS_DIR", prompts_dir)

    improvement = {"improved_suggest_prompt": "新しいプロンプト本文"}
    changed = apply_improvement(improvement)

    assert len(changed) == 1
    assert changed[0] == suggest_md
    updated_content = suggest_md.read_text(encoding="utf-8")
    assert "新しいプロンプト本文" in updated_content
    assert "古いプロンプト" not in updated_content


def test_apply_improvement_no_change_when_same(tmp_path: Path, monkeypatch):
    """差分がない場合（同一本文）はファイルを変更せず空リストを返すこと"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    suggest_md = prompts_dir / "suggest.md"
    body = "同じプロンプト本文"
    original_content = (
        f"<!-- PROMPT:START -->\n{body}\n<!-- PROMPT:END -->\n"
    )
    suggest_md.write_text(original_content, encoding="utf-8")

    import scripts.auto_improve as module
    monkeypatch.setattr(module, "PROMPTS_DIR", prompts_dir)

    improvement = {"improved_suggest_prompt": body}
    changed = apply_improvement(improvement)
    assert changed == []


def test_apply_improvement_returns_empty_when_no_suggest_md(tmp_path: Path, monkeypatch):
    """suggest.md が存在しない場合は空リストを返すこと"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    import scripts.auto_improve as module
    monkeypatch.setattr(module, "PROMPTS_DIR", prompts_dir)

    improvement = {"improved_suggest_prompt": "何かしらのプロンプト"}
    changed = apply_improvement(improvement)
    assert changed == []


def test_apply_improvement_returns_empty_when_empty_prompt(tmp_path: Path, monkeypatch):
    """improved_suggest_prompt が空文字の場合は空リストを返すこと"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    suggest_md = prompts_dir / "suggest.md"
    suggest_md.write_text("<!-- PROMPT:START -->\n何か\n<!-- PROMPT:END -->\n", encoding="utf-8")

    import scripts.auto_improve as module
    monkeypatch.setattr(module, "PROMPTS_DIR", prompts_dir)

    improvement = {"improved_suggest_prompt": ""}
    changed = apply_improvement(improvement)
    assert changed == []


# ---------------------------------------------------------------------------
# create_branch_and_pr のテスト
# ---------------------------------------------------------------------------

def test_create_branch_and_pr_no_files(failing_results: dict, dry_run_improvement: dict):
    """変更ファイルがない場合は None を返すこと"""
    result = create_branch_and_pr(failing_results, dry_run_improvement, [])
    assert result is None


def test_create_branch_and_pr_calls_git_and_gh(
    tmp_path: Path, failing_results: dict, dry_run_improvement: dict, monkeypatch
):
    """変更ファイルがある場合、git・gh コマンドを呼び出すこと"""
    # PROJECT_ROOT を tmp_path に向ける
    import scripts.auto_improve as module
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)

    changed_file = tmp_path / "prompts" / "suggest.md"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text("テスト用プロンプト", encoding="utf-8")

    call_log: list[list[str]] = []

    def mock_run(args: list[str], **kwargs):
        call_log.append(args)
        mock_result = MagicMock()
        if args[0] == "gh" and "create" in args:
            mock_result.stdout = "https://github.com/TomorrowsMealPlanningBoard/webapp/pull/999"
        else:
            mock_result.stdout = ""
        mock_result.returncode = 0
        return mock_result

    monkeypatch.setattr(subprocess, "run", mock_run)

    pr_url = create_branch_and_pr(failing_results, dry_run_improvement, [changed_file])

    assert pr_url == "https://github.com/TomorrowsMealPlanningBoard/webapp/pull/999"

    # git checkout -b が呼ばれること
    git_checkout_calls = [c for c in call_log if c[:2] == ["git", "checkout"]]
    assert len(git_checkout_calls) >= 1

    # gh pr create --draft が呼ばれること
    gh_calls = [c for c in call_log if c[0] == "gh" and "create" in c]
    assert len(gh_calls) == 1
    assert "--draft" in gh_calls[0]


def test_create_branch_and_pr_handles_gh_failure(
    tmp_path: Path, failing_results: dict, dry_run_improvement: dict, monkeypatch
):
    """gh pr create が失敗した場合でも None を返してクラッシュしないこと"""
    import scripts.auto_improve as module
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)

    changed_file = tmp_path / "prompts" / "suggest.md"
    changed_file.parent.mkdir(parents=True)
    changed_file.write_text("テスト用プロンプト", encoding="utf-8")

    def mock_run(args: list[str], **kwargs):
        if args[0] == "gh" and "create" in args:
            raise subprocess.CalledProcessError(1, args, stderr="error")
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.returncode = 0
        return mock_result

    monkeypatch.setattr(subprocess, "run", mock_run)

    pr_url = create_branch_and_pr(failing_results, dry_run_improvement, [changed_file])
    assert pr_url is None


# ---------------------------------------------------------------------------
# notify_slack のテスト
# ---------------------------------------------------------------------------

def test_notify_slack_skips_when_no_webhook(
    failing_results: dict, dry_run_improvement: dict, monkeypatch
):
    """SLACK_WEBHOOK_URL が未設定の場合はスキップすること"""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    # urlopen が呼ばれないことを確認
    with patch("urllib.request.urlopen") as mock_urlopen:
        notify_slack(failing_results, dry_run_improvement, "https://example.com/pr/1")
        mock_urlopen.assert_not_called()


def test_notify_slack_sends_when_webhook_set(
    failing_results: dict, dry_run_improvement: dict, monkeypatch
):
    """SLACK_WEBHOOK_URL が設定されている場合は POST を送信すること"""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        notify_slack(failing_results, dry_run_improvement, "https://example.com/pr/1")
        mock_urlopen.assert_called_once()

    # リクエストの内容を確認
    call_args = mock_urlopen.call_args[0][0]
    assert isinstance(call_args, urllib.request.Request)
    assert call_args.get_method() == "POST"


def test_notify_slack_includes_pr_url(
    failing_results: dict, dry_run_improvement: dict, monkeypatch
):
    """Slack メッセージに PR URL が含まれること"""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
    pr_url = "https://github.com/TomorrowsMealPlanningBoard/webapp/pull/999"

    sent_data: list[bytes] = []

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200

    def capture_urlopen(req, **kwargs):
        sent_data.append(req.data)
        return mock_response

    with patch("urllib.request.urlopen", side_effect=capture_urlopen):
        notify_slack(failing_results, dry_run_improvement, pr_url)

    assert len(sent_data) == 1
    payload = json.loads(sent_data[0].decode("utf-8"))
    assert pr_url in payload["text"]


def test_notify_slack_handles_network_error(
    failing_results: dict, dry_run_improvement: dict, monkeypatch
):
    """ネットワークエラーが発生しても例外を送出せずログを出すだけであること"""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")

    with patch("urllib.request.urlopen", side_effect=OSError("network error")):
        # 例外が出ないこと
        notify_slack(failing_results, dry_run_improvement, None)


def test_notify_slack_handles_none_pr_url(
    failing_results: dict, dry_run_improvement: dict, monkeypatch
):
    """PR URL が None の場合でもメッセージを送信できること"""
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.status = 200

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        notify_slack(failing_results, dry_run_improvement, None)
        mock_urlopen.assert_called_once()


# ---------------------------------------------------------------------------
# 統合テスト（main 相当のフロー）
# ---------------------------------------------------------------------------

def test_full_flow_dry_run(tmp_path: Path, monkeypatch):
    """DRY_RUN=true でフルフローが正常に完了すること"""
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    # eval_results.json を用意
    eval_file = tmp_path / "eval_results.json"
    eval_data = {
        "timestamp": "2026-01-01T00:00:00Z",
        "scores": [
            {"test_case_id": "tc-001", "score": 4.0, "reason": "テスト低スコア"}
        ],
        "average_score": 4.0,
        "threshold": 6.0,
        "passed": False,
    }
    eval_file.write_text(json.dumps(eval_data), encoding="utf-8")

    # prompts/suggest.md を用意
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "suggest.md").write_text(
        "# テスト\n<!-- PROMPT:START -->\n古いプロンプト\n<!-- PROMPT:END -->\n",
        encoding="utf-8",
    )

    import scripts.auto_improve as module
    monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(module, "PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr(module, "EVAL_RESULTS_PATH", eval_file)

    # git・gh コマンドをモック
    def mock_run(args: list[str], **kwargs):
        mock_result = MagicMock()
        if args[0] == "gh" and "create" in args:
            mock_result.stdout = "https://github.com/test/pull/1"
        else:
            mock_result.stdout = ""
        mock_result.returncode = 0
        return mock_result

    monkeypatch.setattr(subprocess, "run", mock_run)

    from scripts.auto_improve import main
    exit_code = main(eval_file)
    assert exit_code == 0


def test_full_flow_passed_score_no_pr(tmp_path: Path, monkeypatch):
    """スコアが閾値を上回っている場合は PR を作成しないこと"""
    monkeypatch.setenv("DRY_RUN", "true")

    eval_file = tmp_path / "eval_results.json"
    eval_data = {
        "timestamp": "2026-01-01T00:00:00Z",
        "scores": [{"test_case_id": "tc-001", "score": 8.0, "reason": "良好"}],
        "average_score": 8.0,
        "threshold": 6.0,
        "passed": True,
    }
    eval_file.write_text(json.dumps(eval_data), encoding="utf-8")

    import scripts.auto_improve as module
    monkeypatch.setattr(module, "EVAL_RESULTS_PATH", eval_file)

    called_gh = []

    def mock_run(args: list[str], **kwargs):
        if args[0] == "gh":
            called_gh.append(args)
        return MagicMock(stdout="", returncode=0)

    monkeypatch.setattr(subprocess, "run", mock_run)

    from scripts.auto_improve import main
    exit_code = main(eval_file)
    assert exit_code == 0
    assert called_gh == [], "スコアが閾値以上の場合 gh コマンドは呼ばれないこと"
