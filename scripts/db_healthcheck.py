"""
db_healthcheck.py — AlloyDB Auth Proxy 経由の IAM 認証接続確認スクリプト (Issue #28)

AlloyDB Language Connector（google-cloud-alloydb-connector）を使い、
IAM認証（パスワードレス）でAlloyDBインスタンスに接続できるかを確認する。
サイドカーのAuth Proxyプロセスを別途起動する運用でも、Language Connectorを使う運用でも、
「IAM認証のみで接続できる」ことを保証するのが本スクリプトの目的。

【重要】Issue #28 時点ではAlloyDBインスタンスがまだ作成されていない
（infra/terraform/ のIaCコードのみ用意され、terraform apply はユーザーが別途実行する）。
そのため、このスクリプトは以下の2フェーズで動作する:

  1. 接続先が未設定（環境変数 ALLOYDB_INSTANCE_URI が無い）場合:
     「設定が必要です」という分かりやすいメッセージを出して終了コード2で終わる。
     CI/ローカルで誤って実行してもクラッシュせず、意図が伝わるようにする。

  2. 接続先が設定されている場合:
     Auth Proxy（Language Connector）経由でIAM認証接続を試み、
     `SELECT 1` と `SELECT extname FROM pg_extension WHERE extname = 'vector'` を実行して
     pgvector拡張が有効かどうかも報告する。

使い方:
  # インスタンス作成後（terraform apply 実行後）に、outputsの値を使って実行する
  export ALLOYDB_INSTANCE_URI="projects/<P>/locations/<R>/clusters/<C>/instances/<I>"
  export ALLOYDB_DATABASE="tomorrows_meal"
  # 省略時はADCのプリンシパルを使用する
  export ALLOYDB_IAM_USER="cloud-run-sa@PROJECT.iam.gserviceaccount.com"
  uv run python scripts/db_healthcheck.py

終了コード:
  0 = 接続成功（pgvector有効時はその旨も表示）
  1 = 接続を試みたが失敗した
  2 = 設定不足（環境変数未設定）で接続を試みなかった
"""
from __future__ import annotations

import os
import sys


def _missing_config_message() -> str:
    return (
        "\n[db_healthcheck] ALLOYDB_INSTANCE_URI が未設定です。\n"
        "AlloyDBインスタンスがまだプロビジョニングされていない場合はこれが期待される動作です。\n"
        "infra/terraform/ を `terraform apply`（ユーザーが手動実行）した後、\n"
        "`terraform output alloydb_primary_instance_name` の値を使って以下を設定してください:\n\n"
        "  export ALLOYDB_INSTANCE_URI=\"projects/<P>/locations/<R>/clusters/<C>/instances/<I>\"\n"
        "  export ALLOYDB_DATABASE=\"tomorrows_meal\"\n"
        "  # IAM認証ユーザー（省略時はADC実行者のメールアドレスを使用）\n"
        "  export ALLOYDB_IAM_USER=\"cloud-run-sa@<PROJECT>.iam.gserviceaccount.com\"\n\n"
        "詳細は docs/alloydb_provisioning.md を参照してください。\n"
    )


def run_healthcheck() -> int:
    instance_uri = os.getenv("ALLOYDB_INSTANCE_URI")
    if not instance_uri:
        print(_missing_config_message())
        return 2

    database = os.getenv("ALLOYDB_DATABASE", "tomorrows_meal")
    iam_user = os.getenv("ALLOYDB_IAM_USER")  # None -> connector が ADC のプリンシパルを使う

    try:
        from google.cloud.alloydb.connector import Connector, IPTypes
    except ImportError:
        print(
            "[db_healthcheck] google-cloud-alloydb-connector が見つかりません。\n"
            "`uv sync` を実行して依存関係をインストールしてください。",
            file=sys.stderr,
        )
        return 1

    try:
        import pg8000
    except ImportError:
        print(
            "[db_healthcheck] pg8000 が見つかりません。`uv sync` を実行してください。",
            file=sys.stderr,
        )
        return 1

    ip_type_name = os.getenv("ALLOYDB_IP_TYPE", "PRIVATE").upper()
    try:
        ip_type = IPTypes[ip_type_name]
    except KeyError:
        print(
            f"[db_healthcheck] ALLOYDB_IP_TYPE='{ip_type_name}' は不正な値です "
            f"(PRIVATE または PUBLIC を指定してください)。",
            file=sys.stderr,
        )
        return 2

    print(f"[db_healthcheck] 接続先: {instance_uri} (database={database}, ip_type={ip_type_name})")
    if iam_user:
        print(f"[db_healthcheck] IAM認証ユーザー: {iam_user}")
    else:
        print("[db_healthcheck] IAM認証ユーザー: 未指定（ADCの実行プリンシパルを使用）")

    connector = Connector()

    def _getconn() -> "pg8000.dbapi.Connection":
        connect_kwargs = {
            "db": database,
            "enable_iam_auth": True,
            "ip_type": ip_type,
        }
        if iam_user:
            connect_kwargs["user"] = iam_user
        return connector.connect(instance_uri, "pg8000", **connect_kwargs)

    try:
        conn = _getconn()
    except Exception as exc:  # noqa: BLE001 — 接続失敗の理由をそのままユーザーに見せる
        print(f"[db_healthcheck] 接続に失敗しました: {exc}", file=sys.stderr)
        connector.close()
        return 1

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        print("[db_healthcheck] SELECT 1 に成功しました。IAM認証接続は正常です。")

        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        row = cursor.fetchone()
        if row:
            print(
                "[db_healthcheck] pgvector 拡張は有効化されています"
                "（層3のベクトル検索が利用可能）。"
            )
        else:
            print(
                "[db_healthcheck] 警告: pgvector 拡張が未有効化です。\n"
                "  scripts/migrate.py を実行して"
                " `CREATE EXTENSION IF NOT EXISTS vector;` を適用してください。"
            )
        cursor.close()
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[db_healthcheck] クエリ実行中にエラーが発生しました: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
        connector.close()


def main() -> None:
    sys.exit(run_healthcheck())


if __name__ == "__main__":
    main()
