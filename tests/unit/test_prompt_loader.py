"""
Issue #33: プロンプトローダーのユニットテスト。

嗜好抽出/その他エージェントのプロンプトを Git 管理下の `prompts/*.md` から読み込み、
バージョン（Gitコミットハッシュ、または未コミット時のフォールバック識別子）を
取得できることを検証する。
"""
import subprocess

import pytest

from app.prompt_loader import PROMPTS_DIR, PromptNotFoundError, load_prompt


@pytest.fixture(autouse=True)
def _clear_cache():
    """各テストの前後でプロンプトローダーのキャッシュをクリアする"""
    from app.prompt_loader import _load_cached
    _load_cached.cache_clear()
    yield
    _load_cached.cache_clear()


# ------------------------------------------------------------------ helpers --

def _is_git_available() -> bool:
    try:
        subprocess.run(
            ["git", "--version"], capture_output=True, timeout=5, check=False
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# ------------------------------------------------------------------ tests --

def test_prompts_directory_exists():
    """prompts/ ディレクトリがGit管理下のプロジェクトルートに存在すること"""
    assert PROMPTS_DIR.exists()
    assert PROMPTS_DIR.is_dir()


def test_preference_extraction_prompt_file_exists():
    """嗜好抽出プロンプトファイルが切り出されていること（AC1）"""
    path = PROMPTS_DIR / "preference_extraction.md"
    assert path.exists(), "prompts/preference_extraction.md が存在しません"


def test_vision_analysis_prompt_file_exists():
    """Vision Analyzer が実際に読み込む冷蔵庫認識プロンプトファイルが存在すること"""
    path = PROMPTS_DIR / "vision_analysis.md"
    assert path.exists(), "prompts/vision_analysis.md が存在しません"


def test_load_prompt_returns_text_and_version():
    """load_prompt() がプロンプト本文とバージョンを返すこと（AC2, AC4）"""
    result = load_prompt("vision_analysis")
    assert result.name == "vision_analysis"
    assert isinstance(result.text, str)
    assert len(result.text) > 0
    assert isinstance(result.version, str)
    assert len(result.version) > 0


def test_load_prompt_extracts_body_between_markers():
    """PROMPT:START / PROMPT:END マーカー間の本文のみを抽出し、説明文を含まないこと"""
    result = load_prompt("vision_analysis")
    assert "冷蔵庫の写真を分析する食材認識AI" in result.text
    # ファイル冒頭の見出し（マーカー外の説明文）は本文に含まれない
    assert "# Vision Analyzer Agent" not in result.text


def test_load_prompt_missing_file_raises():
    """存在しないプロンプト名を指定すると PromptNotFoundError を送出すること"""
    with pytest.raises(PromptNotFoundError):
        load_prompt("does_not_exist_prompt")


def test_load_prompt_version_is_deterministic_for_same_content():
    """同一ファイルを複数回読み込んでも同じバージョンが返ること（再現性）"""
    first = load_prompt("preference_extraction", use_cache=False)
    second = load_prompt("preference_extraction", use_cache=False)
    assert first.version == second.version


@pytest.mark.skipif(not _is_git_available(), reason="git コマンドが利用できない環境")
def test_load_prompt_version_matches_git_log_when_committed(tmp_path, monkeypatch):
    """
    Gitにコミット済みのプロンプトファイルの場合、バージョンは `git log` の
    最新コミットハッシュと一致すること。
    一時的なGitリポジトリを作成して検証する（実リポジトリの状態に依存しないため）。
    """
    repo_dir = tmp_path / "repo"
    prompts_subdir = repo_dir / "prompts"
    prompts_subdir.mkdir(parents=True)

    def run_git(*args):
        subprocess.run(
            ["git", *args], cwd=repo_dir, check=True, capture_output=True, text=True
        )

    run_git("init", "-q")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")

    prompt_file = prompts_subdir / "sample.md"
    prompt_file.write_text(
        "# サンプル\n<!-- PROMPT:START -->\nこんにちは\n<!-- PROMPT:END -->\n",
        encoding="utf-8",
    )
    run_git("add", "prompts/sample.md")
    run_git("commit", "-q", "-m", "add sample prompt")

    expected_hash = subprocess.run(
        ["git", "log", "-n", "1", "--pretty=format:%H", "--", "prompts/sample.md"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    monkeypatch.setattr("app.prompt_loader.PROMPTS_DIR", prompts_subdir)
    from app.prompt_loader import _load_cached
    _load_cached.cache_clear()

    result = load_prompt("sample", use_cache=False)
    assert result.version == expected_hash


def test_load_prompt_falls_back_to_content_hash_when_uncommitted(tmp_path, monkeypatch):
    """
    Git管理外（未コミット）のプロンプトファイルの場合でも、バージョンとして
    一意な識別子（フォールバック）を返すこと。
    """
    isolated_dir = tmp_path / "not_a_repo"
    isolated_dir.mkdir()
    prompt_file = isolated_dir / "isolated.md"
    prompt_file.write_text(
        "<!-- PROMPT:START -->\n未コミットのプロンプト\n<!-- PROMPT:END -->\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("app.prompt_loader.PROMPTS_DIR", isolated_dir)
    from app.prompt_loader import _load_cached
    _load_cached.cache_clear()

    result = load_prompt("isolated", use_cache=False)
    assert result.version != "unknown"
    assert len(result.version) > 0


def test_load_prompt_uses_cache_by_default():
    """use_cache=True（デフォルト）の場合、同一インスタンスがキャッシュから返ること"""
    first = load_prompt("vision_analysis")
    second = load_prompt("vision_analysis")
    assert first is second
