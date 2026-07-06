"""
migrate.py — AlloyDB(PostgreSQL) へのスキーマ適用スクリプト (Issue #28)

このプロジェクトはまだAlembic等のマイグレーションフレームワークを導入していないため
（app/database.py は開発用SQLiteのみを直接参照する構成）、過剰設計を避けて
「SQLAlchemyの既存モデル定義（app/models.py, #13）をそのままAlloyDBに反映する」
シンプルなスクリプトとして実装する。

行うこと:
  1. `CREATE EXTENSION IF NOT EXISTS vector;` — SPEC.md 層3のベクトルカラムに必要（pgvector）
  2. `Base.metadata.create_all()` — app/models.py の全テーブル（#13 スキーマ）を作成
  3. 層3専用テーブル `recipe_snippets` を作成（vector型カラムを含む）。
     app.agents.context_retriever.RecipeSnippet の本番実装（pgvector版）が
     このテーブルを読み書きする想定。

接続方式:
  IAM認証（パスワードレス）でAlloyDB Auth Proxy(Language Connector)経由で接続する。
  scripts/db_healthcheck.py と同じ接続ヘルパーを共有する。

使い方:
  export ALLOYDB_INSTANCE_URI="projects/<P>/locations/<R>/clusters/<C>/instances/<I>"
  # 値は `terraform output alloydb_primary_instance_name` で取得できる
  export ALLOYDB_DATABASE="tomorrows_meal"
  export ALLOYDB_IAM_USER="cloud-run-sa@<PROJECT>.iam.gserviceaccount.com"
  uv run python scripts/migrate.py

  # 埋め込みの次元数を変更する場合（デフォルト768 = text-embedding-004相当）
  ALLOYDB_VECTOR_DIM=768 uv run python scripts/migrate.py

終了コード:
  0 = 適用成功
  1 = 適用失敗
  2 = 設定不足（環境変数未設定）
"""
from __future__ import annotations

import os
import sys

# app.models を読み込むために、リポジトリルートをパスに追加する
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RECIPE_SNIPPETS_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS recipe_snippets (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NULL REFERENCES users(uid) ON DELETE CASCADE,
    text TEXT NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'unknown',
    tags JSONB NULL,
    embedding vector({dim}) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

RECIPE_SNIPPETS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS recipe_snippets_embedding_idx
ON recipe_snippets
USING hnsw (embedding vector_cosine_ops);
"""


def _missing_config_message() -> str:
    return (
        "\n[migrate] ALLOYDB_INSTANCE_URI が未設定です。\n"
        "AlloyDBインスタンスがまだプロビジョニングされていない場合はこれが期待される動作です。\n"
        "infra/terraform/ を `terraform apply`（ユーザーが手動実行）した後に\n"
        "本スクリプトを実行してください。\n"
        "詳細は docs/alloydb_provisioning.md を参照してください。\n"
    )


def _get_connection():
    """scripts/db_healthcheck.py と同じ接続方式（IAM認証・Language Connector）で接続を取得する。"""
    from google.cloud.alloydb.connector import Connector, IPTypes

    instance_uri = os.environ["ALLOYDB_INSTANCE_URI"]
    database = os.getenv("ALLOYDB_DATABASE", "tomorrows_meal")
    iam_user = os.getenv("ALLOYDB_IAM_USER")
    ip_type = IPTypes[os.getenv("ALLOYDB_IP_TYPE", "PRIVATE").upper()]

    connector = Connector()
    connect_kwargs = {"db": database, "enable_iam_auth": True, "ip_type": ip_type}
    if iam_user:
        connect_kwargs["user"] = iam_user
    conn = connector.connect(instance_uri, "pg8000", **connect_kwargs)
    return connector, conn


def run_migration() -> int:
    if "ALLOYDB_INSTANCE_URI" not in os.environ:
        print(_missing_config_message())
        return 2

    try:
        connector, conn = _get_connection()
    except Exception as exc:  # noqa: BLE001
        print(f"[migrate] 接続に失敗しました: {exc}", file=sys.stderr)
        return 1

    try:
        conn.autocommit = True
        cursor = conn.cursor()

        print("[migrate] CREATE EXTENSION IF NOT EXISTS vector; を適用します...")
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        print("[migrate] pgvector 拡張を有効化しました。")

        print("[migrate] app/models.py（#13スキーマ）のテーブルを作成します...")
        _create_all_sqlalchemy_tables(cursor)
        print("[migrate] users / inventories / meal_histories / feedbacks / "
              "meal_proposals / quality_score_logs テーブルを作成しました。")

        dim = os.getenv("ALLOYDB_VECTOR_DIM", "768")
        print(f"[migrate] recipe_snippets（層3 pgvectorテーブル, dim={dim}）を作成します...")
        cursor.execute(RECIPE_SNIPPETS_DDL_TEMPLATE.format(dim=dim))
        cursor.execute(RECIPE_SNIPPETS_INDEX_DDL)
        print("[migrate] recipe_snippets テーブルとHNSWインデックスを作成しました。")

        cursor.close()
        print("[migrate] マイグレーション適用が完了しました。")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[migrate] マイグレーション適用中にエラーが発生しました: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
        connector.close()


def _create_all_sqlalchemy_tables(cursor) -> None:
    """
    app/models.py の Base.metadata から CREATE TABLE 文を生成し、psycopg互換カーソルで実行する。
    SQLAlchemyのcreate_all()はengineを要求するため、ここではDDLコンパイルのみ利用し、
    実行自体は本スクリプトが保持するIAM認証コネクションで行う（app.database.engineは
    SQLite固定のため使わない）。
    """
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    import app.models  # noqa: F401 — Base.metadata にテーブルを登録するためのimport
    from app.database import Base

    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table, if_not_exists=True).compile(dialect=dialect))
        cursor.execute(ddl)


def main() -> None:
    sys.exit(run_migration())


if __name__ == "__main__":
    main()
