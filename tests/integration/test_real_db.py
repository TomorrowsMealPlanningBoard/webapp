"""
インテグレーションテスト: 本物のDBに接続して動作確認する。

【重要】このファイルはCIで自動実行しない。ローカル確認用。
  - AlloyDB / 本番DB接続が必要な場合に手動で実行する
  - 実行前に環境変数 DATABASE_URL をセットすること

実行方法:
  DATABASE_URL=postgresql://... uv run pytest tests/integration/ -v
"""
import pytest
import os

pytestmark = pytest.mark.skipif(
    "DATABASE_URL" not in os.environ,
    reason="DATABASE_URL が未設定のためスキップ（ローカル確認用）"
)


def test_placeholder():
    """インテグレーションテストの雛形。実装時にここに追加する。"""
    pass
