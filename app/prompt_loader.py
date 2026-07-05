"""
プロンプトローダー — Git管理下の `prompts/*.md` からプロンプト本文を読み込む汎用ユーティリティ。

SPEC.md §4「真のDevOpsを実現する2つのループ」のループB（バージョン管理）を実現する基盤。
エージェントのプロンプトをコードに直書きせず、Markdownファイルとして切り出すことで、
- 変更が diff/PR でレビュー可能な粒度になる
- プロンプトのバージョン（Gitコミットハッシュ）を提案ログに紐づけて記録できる
- 将来的な GitHub Actions 定期eval（LLM-as-judge）や自動起票の対象にできる

使い方:
    from app.prompt_loader import load_prompt

    prompt = load_prompt("vision_analysis")
    prompt.text     # プロンプト本文（str）
    prompt.version  # このプロンプトファイルの最新変更コミットハッシュ（Git管理外なら "unknown"）
    prompt.path     # 読み込んだファイルの絶対パス
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# プロジェクトルート直下の prompts/ ディレクトリ
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_PROMPT_START = "<!-- PROMPT:START -->"
_PROMPT_END = "<!-- PROMPT:END -->"


class PromptNotFoundError(FileNotFoundError):
    """指定した名前のプロンプトファイルが存在しない場合に送出する。"""


@dataclass(frozen=True)
class LoadedPrompt:
    """読み込んだプロンプトと、そのバージョン情報を保持する。"""

    name: str
    text: str
    version: str
    path: Path


def _prompt_path(name: str) -> Path:
    return PROMPTS_DIR / f"{name}.md"


def _extract_prompt_body(markdown_text: str) -> str:
    """
    Markdownファイルから `<!-- PROMPT:START -->` 〜 `<!-- PROMPT:END -->` の
    区間のみを抽出する。マーカーが無い場合はファイル全体を返す
    （説明文を含まないシンプルなプロンプトファイルにも対応するため）。
    """
    start = markdown_text.find(_PROMPT_START)
    end = markdown_text.find(_PROMPT_END)
    if start == -1 or end == -1 or end <= start:
        return markdown_text.strip()
    return markdown_text[start + len(_PROMPT_START):end].strip()


def _git_file_hash(path: Path) -> str:
    """
    ファイルの最新変更コミットハッシュを取得する。
    Git管理外（未コミット・リポジトリ外・git未インストール等）の場合は
    ファイル内容のSHA256から算出した疑似バージョンにフォールバックする。
    """
    try:
        result = subprocess.run(
            ["git", "log", "-n", "1", "--pretty=format:%H", "--", str(path)],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit_hash = result.stdout.strip()
        if result.returncode == 0 and commit_hash:
            return commit_hash
    except (OSError, subprocess.SubprocessError):
        pass

    # フォールバック: 未コミットの変更やGit管理外環境でもバージョンを一意に識別できるようにする
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"unversioned:{digest[:12]}"
    except OSError:
        return "unknown"


@lru_cache(maxsize=None)
def _load_cached(name: str) -> LoadedPrompt:
    path = _prompt_path(name)
    if not path.exists():
        raise PromptNotFoundError(f"プロンプトファイルが見つかりません: {path}")

    raw_text = path.read_text(encoding="utf-8")
    body = _extract_prompt_body(raw_text)
    version = _git_file_hash(path)
    return LoadedPrompt(name=name, text=body, version=version, path=path)


def load_prompt(name: str, *, use_cache: bool = True) -> LoadedPrompt:
    """
    `prompts/<name>.md` を読み込み、本文とバージョン（Gitコミットハッシュ）を返す。

    Args:
        name: 拡張子を除いたプロンプトファイル名（例: "vision_analysis"）
        use_cache: True の場合、プロセス内でキャッシュされた結果を返す
                   （プロンプトファイルの再読み込み・Git呼び出しコストを削減する）。
                   テストで最新内容を強制的に読み直したい場合は False を指定する。

    Returns:
        LoadedPrompt: プロンプト本文とバージョン情報

    Raises:
        PromptNotFoundError: 指定した名前のプロンプトファイルが存在しない場合
    """
    if use_cache:
        return _load_cached(name)

    _load_cached.cache_clear()
    return _load_cached(name)
