"""
Issue #28: scripts/db_healthcheck.py のユニットテスト。

実際のAlloyDBインスタンスはまだ存在しないため（terraform apply未実行）、
ネットワーク接続を伴わないロジック部分（設定不足時の挙動、IP種別の検証）のみを検証する。
"""
import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

db_healthcheck = importlib.import_module("db_healthcheck")


@pytest.fixture(autouse=True)
def _clear_alloydb_env(monkeypatch):
    """テスト間でAlloyDB関連の環境変数が漏れないようにする。"""
    for key in [
        "ALLOYDB_INSTANCE_URI",
        "ALLOYDB_DATABASE",
        "ALLOYDB_IAM_USER",
        "ALLOYDB_IP_TYPE",
    ]:
        monkeypatch.delenv(key, raising=False)
    yield


def test_run_healthcheck_returns_2_when_instance_uri_missing():
    """ALLOYDB_INSTANCE_URI が未設定の場合、接続を試みず終了コード2を返す。"""
    exit_code = db_healthcheck.run_healthcheck()
    assert exit_code == 2


def test_run_healthcheck_returns_2_on_invalid_ip_type(monkeypatch):
    """不正なALLOYDB_IP_TYPEを指定した場合、接続処理に進む前に終了コード2で止まる。"""
    monkeypatch.setenv("ALLOYDB_INSTANCE_URI", "projects/p/locations/r/clusters/c/instances/i")
    monkeypatch.setenv("ALLOYDB_IP_TYPE", "NOT_A_REAL_TYPE")
    exit_code = db_healthcheck.run_healthcheck()
    assert exit_code == 2


def test_missing_config_message_mentions_terraform_apply():
    """設定不足時のメッセージが、terraform applyの実行が前提であることを明示している。"""
    message = db_healthcheck._missing_config_message()
    assert "terraform apply" in message
    assert "ALLOYDB_INSTANCE_URI" in message
