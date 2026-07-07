"""
db_healthcheck.py — 新データ基盤（Firestore / Memory Bank）の疎通確認スクリプト (Issue #79)

Epic #75 のアーキテクチャ移行に伴い、AlloyDB(pgvector) 前提の疎通確認（旧実装）を
Firestore（層1/層2/層3'の構造化DB）と Memory Bank（層3の好み学習）向けに置き換えた。
いずれもCloud Run実行サービスアカウントのIAM（ADC）のみで接続でき、Auth Proxyのような
サイドカーは不要（SPEC.md §6.4）。

使い方:
  # Firestore疎通確認（USE_FIRESTORE=true 運用時）
  export GOOGLE_CLOUD_PROJECT="<project-id>"
  uv run python scripts/db_healthcheck.py --target firestore

  # Memory Bank疎通確認（USE_MEMORY_BANK=true 運用時。Agent Engineのプロビジョニングが必要）
  export GOOGLE_CLOUD_PROJECT="<project-id>"
  export MEMORY_BANK_AGENT_ENGINE_ID="<agent-engine-id>"
  uv run python scripts/db_healthcheck.py --target memory_bank

終了コード:
  0 = 接続成功
  1 = 接続を試みたが失敗した
  2 = 設定不足（環境変数未設定）で接続を試みなかった
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# app.agents を読み込むために、リポジトリルートをパスに追加する
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _missing_project_message() -> str:
    return (
        "\n[db_healthcheck] GOOGLE_CLOUD_PROJECT が未設定です。\n"
        "export GOOGLE_CLOUD_PROJECT=\"<project-id>\" を設定してください。\n"
    )


def check_firestore() -> int:
    """Firestore（層1/層2/層3'の構造化DB）への疎通を確認する。"""
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        print(_missing_project_message())
        return 2

    try:
        from google.cloud import firestore
    except ImportError:
        print(
            "[db_healthcheck] google-cloud-firestore が見つかりません。"
            "`uv sync` を実行してください。",
            file=sys.stderr,
        )
        return 1

    print(f"[db_healthcheck] Firestore 接続先: project={project}")
    try:
        client = firestore.Client(project=project)
        # 疎通確認のみ。実データへの書き込みは行わない（読み取り専用のlist操作）。
        list(client.collections())
        print("[db_healthcheck] Firestore への接続に成功しました。")
        return 0
    except Exception as exc:  # noqa: BLE001 — 接続失敗の理由をそのままユーザーに見せる
        print(f"[db_healthcheck] Firestore への接続に失敗しました: {exc}", file=sys.stderr)
        return 1


def check_memory_bank() -> int:
    """Memory Bank（層3の好み学習、Agent Platform）への疎通を確認する。"""
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        print(_missing_project_message())
        return 2

    agent_engine_id = os.getenv("MEMORY_BANK_AGENT_ENGINE_ID")
    if not agent_engine_id:
        print(
            "\n[db_healthcheck] MEMORY_BANK_AGENT_ENGINE_ID が未設定です。\n"
            "Memory Bank用のAgent Engineをプロビジョニングし、IDを設定してください\n"
            "（embedding_model=gemini-embedding-001の明示指定が必須。Issue #82参照）。\n"
        )
        return 2

    try:
        from app.agents.memory_bank_client import MemoryBankVectorSearchClient
    except ImportError as exc:
        print(
            f"[db_healthcheck] MemoryBankVectorSearchClient の読み込みに失敗: {exc}",
            file=sys.stderr,
        )
        return 1

    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    print(
        f"[db_healthcheck] Memory Bank 接続先: project={project}, location={location}, "
        f"agent_engine_id={agent_engine_id}"
    )
    try:
        client = MemoryBankVectorSearchClient(
            project=project, location=location, agent_engine_id=agent_engine_id
        )
        asyncio.run(client.search(user_id="db-healthcheck", query_text="疎通確認", top_k=1))
        print("[db_healthcheck] Memory Bank への接続に成功しました。")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[db_healthcheck] Memory Bank への接続に失敗しました: {exc}", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=["firestore", "memory_bank"],
        default="firestore",
        help="疎通確認の対象（デフォルト: firestore）",
    )
    args = parser.parse_args()

    if args.target == "firestore":
        sys.exit(check_firestore())
    else:
        sys.exit(check_memory_bank())


if __name__ == "__main__":
    main()
