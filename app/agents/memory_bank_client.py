"""
Memory Bank による好み学習ループ（ループA）の実装（Issue #77）。

Agent Platform Memory Bank（旧 Vertex AI、`VertexAiMemoryBankService`）を使い、
層3（ユーザーFBの自由記述からの好み学習）を実現する。#61 でクローズした
`PgVectorSearchClient`（AlloyDB/pgvector）の代替。

設計方針（SPEC.md §3, Epic #75 に準拠）:
- `VectorSearchClient` Protocol（`context_retriever.py`）を満たす
  `MemoryBankVectorSearchClient` として実装し、Context Retriever Agent からは
  既存の `InMemoryVectorSearchClient` と透過的に差し替え可能にする。
- memory はユーザーごとに `user_id` でスコープ分離される（Memory Bank の標準機能）。
- **層1（アレルギー等のハード制約）は本クライアントに一切書き込まない。**
  決定的フィルタ（`structured_store.py`）と確率的な記憶（Memory Bank）を
  意図的に分離する（CLAUDE.md §0.4 / SPEC §3 ガードレール）。

日本語精度に関する重要な前提（Epic #75 調査結果）:
- Memory Bank のデフォルト embedding モデル `text-embedding-005` は英語専用で、
  日本語FBでは好み学習が機能しない。
- Agent Engine（Memory Bank のバックエンド）プロビジョニング時に
  `context_spec.memory_bank_config.similarity_search_config.embedding_model`
  へ **`gemini-embedding-001`（多言語対応）を明示指定すること**が必須。
  この設定は Agent Engine 作成パラメータであり、本clientの実行時引数では
  変更できないため、インフラ側（Terraform/プロビジョニングスクリプト、#79管轄）
  で必ず設定すること。未設定のAgent Engineをこのクライアントに渡すと、
  日本語の自由記述FBが正しくベクトル化されず好み学習が機能しない事故になる。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, List, Optional

from .context_retriever import RecipeSnippet

logger = logging.getLogger("tomorrows_meal.memory_bank_client")

_DEFAULT_APP_NAME = "tomorrows_meal"


def _get_agent_engine_id() -> str:
    agent_engine_id = os.getenv("MEMORY_BANK_AGENT_ENGINE_ID")
    if not agent_engine_id:
        raise RuntimeError(
            "MEMORY_BANK_AGENT_ENGINE_ID 環境変数が設定されていません。"
            "Memory Bank用のAgent Engineをプロビジョニングし、IDを設定してください"
            "（embedding_model=gemini-embedding-001 の明示指定が必須）。"
        )
    return agent_engine_id


@dataclass
class MemoryBankVectorSearchClient:
    """
    `VectorSearchClient` Protocol を満たす Memory Bank 実装クライアント。

    Memory Bank の `search_memory` はメタデータフィルタ（exclude_tags）を
    ネイティブに持たないため、取得後にクライアント側で negative_tags による
    ハードフィルタを適用する（ハイブリッド検索の要件はここで再現する）。
    """

    app_name: str = _DEFAULT_APP_NAME
    project: Optional[str] = None
    location: Optional[str] = None
    agent_engine_id: Optional[str] = None
    _service: object = None  # 遅延初期化した VertexAiMemoryBankService
    _api_client: object = None  # 使い回す vertexai.Client（本番レイテンシ根治）

    def _resolve_location(self) -> str:
        # Memory Bank(Agent Engine)は us-central1 に存在する。Cloud Run が
        # asia-northeast1 で動いていても、クライアントは必ずエンジンのリージョン
        # (既定 us-central1)を指す必要がある。GEMINI_LIVE_LOCATION は Live API 用の
        # 値が入り得るため、専用の MEMORY_BANK_LOCATION を優先する。
        return (
            self.location
            or os.getenv("MEMORY_BANK_LOCATION")
            or os.getenv("GEMINI_LIVE_LOCATION")
            or "us-central1"
        )

    def _resolve_project(self) -> Optional[str]:
        return self.project or os.getenv("GOOGLE_CLOUD_PROJECT")

    def _get_service(self):
        if self._service is None:
            from google.adk.memory import VertexAiMemoryBankService

            service = VertexAiMemoryBankService(
                project=self._resolve_project(),
                location=self._resolve_location(),
                agent_engine_id=self.agent_engine_id or _get_agent_engine_id(),
            )

            # 本番レイテンシ根治の核心:
            # ADK の VertexAiMemoryBankService._get_api_client() は
            # search_memory / add_memory の **呼び出しごとに** 新しい
            # vertexai.Client(...) を生成する。google-genai の非同期パスは
            # クライアント生成後の初回リクエストで ADC 解決（Cloud Run では
            # メタデータサーバ往復）と TLS/接続確立を毎回やり直すため、
            # CPU スロットリング + クロスリージョン(asia-northeast1→us-central1)
            # の条件下では 1 回あたり 10 秒超まで膨らみタイムアウトしていた。
            #
            # そこで単一の vertexai.Client を生成して使い回すよう
            # _get_api_client を差し替える。接続プール・解決済み ADC が
            # プロセス内で維持され、2 回目以降はサブ秒で完了する
            # （min-instances=1 の常駐インスタンスと相性が良い）。
            import vertexai

            api_client = vertexai.Client(
                project=self._resolve_project(),
                location=self._resolve_location(),
            )
            self._api_client = api_client
            service._get_api_client = lambda: api_client.aio  # type: ignore[method-assign]

            self._service = service
        return self._service

    async def warmup(self) -> None:
        """
        起動時ウォームアップ（FastAPI lifespan から呼ぶ）。
        vertexai クライアント生成・ADC 解決・us-central1 への初回接続確立を
        リクエスト経路の外で済ませておくことで、最初の /api/propose が
        コールドスタート分のレイテンシを負わないようにする。
        検索が空/失敗でも例外は握り潰す（ウォームアップは加点要素であり、
        起動失敗にはしない）。
        """
        try:
            await self.search(user_id="__warmup__", query_text="warmup", top_k=1)
            logger.info("Memory Bank クライアントのウォームアップが完了しました。")
        except BaseException:  # noqa: BLE001 - ウォームアップ失敗は起動を止めない
            logger.warning(
                "Memory Bank クライアントのウォームアップに失敗しました（起動は継続）。",
                exc_info=True,
            )

    async def search(
        self,
        user_id: str,
        query_text: str,
        top_k: int,
        exclude_tags: Iterable[str] = (),
    ) -> List[RecipeSnippet]:
        if not query_text:
            return []

        service = self._get_service()
        response = await service.search_memory(
            app_name=self.app_name, user_id=user_id, query=query_text
        )

        exclude_set = {t.strip() for t in exclude_tags if t and t.strip()}
        snippets: List[RecipeSnippet] = []
        for i, memory in enumerate(response.memories):
            text_parts = [p.text for p in (memory.content.parts or []) if getattr(p, "text", None)]
            text = " ".join(text_parts).strip()
            if not text:
                continue
            # Memory Bank はタグを持たないため、除外タグはテキスト内の部分一致で
            # 保守的にフィルタする（誤って除外食材の記憶を注入しないためのガードレール）。
            if exclude_set and any(tag in text for tag in exclude_set):
                continue
            snippets.append(
                RecipeSnippet(
                    id=f"memory_bank_{user_id}_{i}",
                    text=text,
                    source="memory_bank",
                    score=1.0 - (i / max(len(response.memories), 1)),
                )
            )
        return snippets[:top_k]

    async def generate_memories(self, user_id: str, texts: List[str]) -> None:
        """
        自由記述FB（`POST /api/feedback` の comment 等）を Memory Bank に投入する。
        `direct_contents_source` 相当（Content配列を直接メモリ化）で書き込む。

        層1（アレルギー等）は呼び出し側（main.py）で絶対に渡さないこと
        （decisions: structured_store.py 側で決定的フィルタとして別管理する）。
        """
        texts = [t.strip() for t in texts if t and t.strip()]
        if not texts:
            return

        from google.adk.memory.memory_entry import MemoryEntry
        from google.genai import types

        service = self._get_service()
        memories = [
            MemoryEntry(content=types.Content(parts=[types.Part(text=t)], role="user"))
            for t in texts
        ]
        await service.add_memory(app_name=self.app_name, user_id=user_id, memories=memories)
        logger.info(
            "Memory Bank に %d 件の自由記述FBを投入しました (user_id=%s)", len(memories), user_id
        )


_shared_memory_bank_client: Optional[MemoryBankVectorSearchClient] = None


def build_vector_search_client():
    """
    `USE_MEMORY_BANK` 環境変数に応じてベクトル検索クライアントを切り替えるファクトリ。
    未設定時（デフォルト）は既存の `InMemoryVectorSearchClient` を維持する
    （ローカル開発・既存テストとの共存構成）。

    Memory Bank モードでは **プロセス内で単一インスタンスを使い回す**。
    `ContextRetrieverAgent` はリクエストごとに生成されるが、その都度
    新しい vertexai.Client / ADC 解決が走ると本番でタイムアウトする
    （本モジュール上部の _get_service の説明参照）。単一インスタンスを共有し、
    起動時ウォームアップで温めた接続をリクエスト間で再利用する。
    """
    from .context_retriever import InMemoryVectorSearchClient

    if os.getenv("USE_MEMORY_BANK", "").lower() in ("1", "true", "yes"):
        global _shared_memory_bank_client
        if _shared_memory_bank_client is None:
            _shared_memory_bank_client = MemoryBankVectorSearchClient()
        return _shared_memory_bank_client
    return InMemoryVectorSearchClient()
