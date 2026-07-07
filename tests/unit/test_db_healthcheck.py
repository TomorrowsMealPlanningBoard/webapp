"""
Issue #79: scripts/db_healthcheck.py（Firestore/Memory Bank疎通確認）のユニットテスト。

実際のFirestore/Agent Engineには接続せず、設定不足時の挙動のみを検証する
（ネットワーク接続を伴うテストは行わない）。
"""
import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

db_healthcheck = importlib.import_module("db_healthcheck")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """テスト間で関連環境変数が漏れないようにする。"""
    for key in ["GOOGLE_CLOUD_PROJECT", "MEMORY_BANK_AGENT_ENGINE_ID", "GOOGLE_CLOUD_LOCATION"]:
        monkeypatch.delenv(key, raising=False)
    yield


def test_check_firestore_returns_2_when_project_missing():
    """GOOGLE_CLOUD_PROJECT が未設定の場合、接続を試みず終了コード2を返す。"""
    assert db_healthcheck.check_firestore() == 2


def test_check_memory_bank_returns_2_when_project_missing():
    """GOOGLE_CLOUD_PROJECT が未設定の場合、接続を試みず終了コード2を返す。"""
    assert db_healthcheck.check_memory_bank() == 2


def test_check_memory_bank_returns_2_when_agent_engine_id_missing(monkeypatch):
    """MEMORY_BANK_AGENT_ENGINE_ID が未設定の場合、接続を試みず終了コード2を返す。"""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    assert db_healthcheck.check_memory_bank() == 2
