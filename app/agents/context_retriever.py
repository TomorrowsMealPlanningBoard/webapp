"""
Context Retriever Agent — ハイブリッド検索（メタデータフィルタ + ベクトル検索）により
Recipe Generator への入力コンテキストを1回の呼び出しで統合取得する。

設計方針（SPEC.md §3, §5.2 に準拠）:
- 層1（静的プロファイル / ハード制約: アレルギー・調理器具・禁止食材）は
  **決定的フィルタ（if文による機械的除外）** のみで扱う。ベクトル検索の対象にしない。
- 層2（構造化FB: negative_tags / positive_tags）はメタデータフィルタとして扱う。
  negative_tags はハードフィルタ（絶対的除外）、positive_tags は参考情報（ソフトシグナル）。
- 層3（自由記述FB・外部レシピ）はベクトル検索の対象。ただし層2の negative_tags を
  メタデータフィルタとして組み合わせることで「除外タグを確実に排除するハイブリッド検索」を構成する。

インフラ制約への対応:
- 本番は AlloyDB(pgvector) を想定するが、プロビジョニング（#28）は未着手のため、
  ベクトル検索部分は `VectorSearchClient` Protocol として抽象化し、
  ローカル開発・テストでは `InMemoryVectorSearchClient`（簡易コサイン類似度）を使う。
  将来的に pgvector 実装のクライアントに差し替えるだけで良い設計とする。
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..firestore_store import (
    get_feedbacks_with_comment,
    get_meal_proposals_since,
    get_recipe_sources_completed,
    get_user,
)
from .health_api import HealthData, HealthDataClient
from .structured_store import StructuredStore, FirestoreStructuredStore

logger = logging.getLogger("tomorrows_meal.context_retriever")

# 層3（ベクトル検索 / 本番は Memory Bank）のタイムアウト秒数。
# 本番で Memory Bank 呼び出しが ~7秒ハングして提案全体を巻き添えにしていたため、
# 層3は加点要素であることを踏まえ短めのタイムアウトで打ち切って空フォールバックする。
# 環境変数 VECTOR_SEARCH_TIMEOUT_SEC で上書き可能。
def _get_vector_search_timeout_sec() -> float:
    raw = os.getenv("VECTOR_SEARCH_TIMEOUT_SEC", "5")
    try:
        val = float(raw)
        return val if val > 0 else 5.0
    except (TypeError, ValueError):
        return 5.0

# ============================================================
# 出力型（Recipe Generator Agent への入力コンテキスト構造）
# ============================================================

class HardConstraints(BaseModel):
    """層1: 静的プロファイル / ハード制約。決定的フィルタのみで構築される。"""

    allergies: List[str] = Field(default_factory=list)
    forbidden_ingredients: List[str] = Field(default_factory=list)  # 禁止食材（嫌い/NG食材）
    available_kitchen_tools: List[str] = Field(default_factory=list)


class StructuredFeedbackContext(BaseModel):
    """層2: 構造化FB（メタデータ）。negative_tags はハードフィルタとして扱う。"""

    negative_tags: List[str] = Field(default_factory=list)
    positive_tags: List[str] = Field(default_factory=list)


class RecipeSnippet(BaseModel):
    """層3: ベクトル検索でヒットしたレシピ/自由記述FBの断片。"""

    id: str
    text: str
    source: str = "unknown"  # "user_feedback" | "external_recipe" など
    score: float = 0.0
    tags: List[str] = Field(default_factory=list)


class FavoriteRecipeSource(BaseModel):
    """
    層3': お気に入り外部レシピソース（YouTube/ブログ）から抽出した構造化データ（Issue #78）。
    ベクトル化せず、Recipe Generator 実行時に全件そのままプロンプトへ直接注入する
    （SPEC.md §5.4: 1ユーザーあたり数件〜数十件の小規模のためベクトル検索は過剰設計）。
    """

    seasoning_tendency: str = ""
    favorite_ingredient_combos: List[str] = Field(default_factory=list)
    cooking_style: str = ""
    tags: List[str] = Field(default_factory=list)
    source_title: str = ""
    source_url: str = ""


class RetrievedContext(BaseModel):
    """
    Context Retriever Agent の出力。Recipe Generator Agent への入力コンテキスト構造。
    """

    user_id: str
    hard_constraints: HardConstraints
    structured_feedback: StructuredFeedbackContext
    similar_snippets: List[RecipeSnippet] = Field(default_factory=list)
    # Issue #24: 直近7日以内に提案済みのレシピタイトル一覧。重複提案回避に使用する。
    recent_proposal_titles: List[str] = Field(default_factory=list)
    # Issue #22: 前日の健康データ（Google Fit API）。未連携時は None。
    health_data: Optional[HealthData] = None
    # Issue #78: 層3'（お気に入り外部レシピソース）。ベクトル検索を経由せず全件取得する。
    favorite_recipe_sources: List[FavoriteRecipeSource] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


# ============================================================
# ベクトル検索の抽象インターフェース（将来 pgvector に差し替え可能）
# ============================================================

@runtime_checkable
class VectorSearchClient(Protocol):
    """
    ベクトル検索クライアントの抽象インターフェース。
    本番実装は AlloyDB(pgvector) を想定（#28 プロビジョニング後に差し替え）。
    """

    async def search(
        self,
        user_id: str,
        query_text: str,
        top_k: int,
        exclude_tags: Iterable[str] = (),
    ) -> List[RecipeSnippet]:
        """
        query_text に類似する上位 top_k 件を返す。
        exclude_tags に該当するタグを持つ結果はメタデータフィルタで除外する
        （ハイブリッド検索: ベクトル類似度 × メタデータフィルタ）。
        """
        ...


@dataclass
class InMemoryVectorSearchClient:
    """
    ローカル開発・テスト用の簡易ベクトル検索実装。
    実際の埋め込みモデルは使わず、文字トライグラムベースの疑似ベクトルで
    コサイン類似度を計算する（外部API呼び出し無し・決定的・高速）。

    将来的に AlloyDB(pgvector) を使うクライアントに差し替える際は、
    本クラスと同じ `VectorSearchClient` インターフェースを満たせばよい。
    """

    corpus: List[RecipeSnippet] = field(default_factory=list)

    @staticmethod
    def _vectorize(text: str) -> dict:
        """文字トライグラムの出現頻度による疑似埋め込み。"""
        text = text.lower()
        grams: dict[str, int] = {}
        n = 3
        if len(text) < n:
            grams[text] = 1
        else:
            for i in range(len(text) - n + 1):
                gram = text[i : i + n]
                grams[gram] = grams.get(gram, 0) + 1
        return grams

    @classmethod
    def _cosine_similarity(cls, a: dict, b: dict) -> float:
        if not a or not b:
            return 0.0
        common = set(a.keys()) & set(b.keys())
        dot = sum(a[k] * b[k] for k in common)
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def search(
        self,
        user_id: str,
        query_text: str,
        top_k: int,
        exclude_tags: Iterable[str] = (),
    ) -> List[RecipeSnippet]:
        exclude_set = {t.strip() for t in exclude_tags if t and t.strip()}
        query_vec = self._vectorize(query_text)

        candidates = [
            snippet
            for snippet in self.corpus
            # メタデータフィルタ: 除外タグを確実に排除（ハイブリッド検索の要件）
            if not (exclude_set & set(snippet.tags))
        ]

        scored: List[RecipeSnippet] = []
        for snippet in candidates:
            score = self._cosine_similarity(query_vec, self._vectorize(snippet.text))
            scored.append(snippet.model_copy(update={"score": score}))

        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:top_k]


# ============================================================
# Context Retriever Agent
# ============================================================

class ContextRetrieverAgent:
    """
    層1・層2・層3を1回の呼び出しで統合取得し、RetrievedContext を返す独立エージェント。
    Vision Analyzer Agent と並列実行できるよう async インターフェースを提供する
    （データ収集フェーズの並列処理: SPEC.md §5.2）。
    """

    def __init__(
        self,
        vector_search_client: Optional[VectorSearchClient] = None,
        health_data_client: Optional[HealthDataClient] = None,
        structured_store: Optional[StructuredStore] = None,
    ):
        if vector_search_client is not None:
            self.vector_search_client = vector_search_client
        else:
            from .memory_bank_client import build_vector_search_client

            self.vector_search_client = build_vector_search_client()
        self.health_data_client = health_data_client or HealthDataClient()
        self.structured_store = structured_store or FirestoreStructuredStore()

    # ---- 層1: 決定的フィルタ（ハード制約） -----------------------------

    def _get_hard_constraints(self, user: User) -> HardConstraints:
        """
        層1: 静的プロファイル/ハード制約を構築する。
        `structured_store` からの機械的な読み出しのみで、ベクトル検索・確率的処理は
        一切行わない（Issue #76: 永続先は環境変数 `USE_FIRESTORE` で切替可能）。
        """
        data = self.structured_store.get_hard_constraints(user.uid)
        return HardConstraints(
            allergies=data.allergies,
            forbidden_ingredients=data.forbidden_ingredients,
            available_kitchen_tools=data.available_kitchen_tools,
        )

    # ---- 層2: 構造化FB（メタデータ） -----------------------------------

    def _get_structured_feedback(self, user_id: str) -> StructuredFeedbackContext:
        """
        層2: negative_tags / positive_tags を集約する。
        negative_tags は後続のベクトル検索でハードフィルタとして使われる。
        """
        data = self.structured_store.get_structured_feedback(user_id)
        return StructuredFeedbackContext(
            negative_tags=data.negative_tags,
            positive_tags=data.positive_tags,
        )

    # ---- 直近提案履歴取得（Issue #24） ------------------------------------

    def _get_recent_proposal_titles(self, user_id: str, days: int = 7) -> List[str]:
        """直近 `days` 日以内に提案済みのレシピタイトル一覧を返す。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        proposals = get_meal_proposals_since(user_id, cutoff)
        return [p.recipe_title for p in proposals]

    # ---- 層3: 自由記述FBのDBロード（Issue #21） --------------------------

    def _load_free_text_corpus_from_db(self, user_id: str) -> List[RecipeSnippet]:
        """Firestore から自由記述FB（cooked かつ comment あり）を取得し RecipeSnippet リストを返す。"""
        feedbacks = get_feedbacks_with_comment(user_id)
        snippets = []
        for fb in feedbacks:
            comment = (fb.comment or "").strip()
            if not comment:
                continue
            snippets.append(
                RecipeSnippet(
                    id=f"fb_comment_{fb.id}",
                    text=comment,
                    source="user_feedback",
                    tags=fb.tags or [],
                )
            )
        return snippets

    # ---- 層3': お気に入りレシピソース（外部URL）の全件直接取得（Issue #78） -----

    def _get_favorite_recipe_sources(self, user_id: str) -> List[FavoriteRecipeSource]:
        """
        層3': DBに保存されているお気に入りレシピソース（外部URL）の抽出結果を全件取得する。

        SPEC.md §5.4（方針転換）: 1ユーザーあたり数件〜数十件の小規模のため、
        ベクトル検索・上位N件抽出は行わず、Recipe Generator 実行時に全件そのまま
        プロンプトへ直接注入する。ベクトル検索の対象・コーパスにも一切混入させない。

        件数が将来的に増えて破綻する場合の閾値: 現状は数十件を前提にプロンプト長への
        影響が軽微という判断だが、実運用で1ユーザーあたり数百件を超える場合は
        ベクトル検索（上位N件抽出）の導入を再検討すること（SPEC.md §5.4参照）。
        抽出に失敗した（status="failed"）レコードは含めない。
        """
        sources = get_recipe_sources_completed(user_id)
        result = []
        for src in sources:
            summary = src.extracted_summary or {}
            result.append(
                FavoriteRecipeSource(
                    seasoning_tendency=summary.get("seasoning_tendency", ""),
                    favorite_ingredient_combos=summary.get("favorite_ingredient_combos", []),
                    cooking_style=summary.get("cooking_style", ""),
                    tags=src.tags or [],
                    source_title=src.title or "",
                    source_url=src.url or "",
                )
            )
        return result

    # ---- 層3: ハイブリッド検索（ベクトル + メタデータフィルタ） ----------

    async def _get_similar_snippets(
        self,
        user_id: str,
        query_text: str,
        negative_tags: List[str],
        top_k: int,
    ) -> List[RecipeSnippet]:
        """
        層3: 自由記述FB・外部レシピのベクトル検索上位N件を取得する。
        negative_tags（層2）をメタデータフィルタとして組み合わせ、
        除外タグに該当する結果を確実に排除するハイブリッド検索を構成する。

        注意: 層1のハード制約（アレルギー等）はここでは扱わない。
        層1は決定的フィルタとして別経路（_get_hard_constraints）でのみ適用し、
        ベクトル検索の対象・フィルタ条件に混入させない（SPEC.md §3 の設計思想）。

        フォールバック設計（Issue: /api/propose 500 リグレッション）:
        層3の similar_snippets は SPEC上「好み学習＝加点要素」であり、
        ベクトル検索（本番は Memory Bank）が例外を送出／ハング／キャンセルされても
        提案自体は成立させる。そのため、ここで
          (1) asyncio.wait_for でタイムアウト（既定5秒。本番 Memory Bank の ~7秒ハングを
              打ち切る。goal 4）
          (2) BaseException（asyncio.CancelledError / TimeoutError 含む）まで捕捉
        して空リストにフォールバックする（層1/層2の決定的制約と生成は層3と独立に動ける）。
        層1（アレルギー等ハード制約）には一切影響させない
        ——層1は別経路の決定的フィルタのまま。

        注意: 従来は `except Exception` だったため、上流（ADK ノード）から届く
        asyncio.CancelledError（Exception を継承しない BaseException）を取りこぼす
        恐れがあった。ここは加点要素の隔離が目的なので BaseException で確実に囲む。
        """
        if not query_text:
            logger.info(
                "層3ベクトル検索をスキップ（query_text 空）(user_id=%s)。", user_id
            )
            return []
        timeout_sec = _get_vector_search_timeout_sec()
        logger.info(
            "層3ベクトル検索を開始します (user_id=%s, client=%s, query_len=%d, timeout=%.1fs)。",
            user_id,
            type(self.vector_search_client).__name__,
            len(query_text),
            timeout_sec,
        )
        try:
            result = await asyncio.wait_for(
                self.vector_search_client.search(
                    user_id=user_id,
                    query_text=query_text,
                    top_k=top_k,
                    exclude_tags=negative_tags,
                ),
                timeout=timeout_sec,
            )
            logger.info(
                "層3ベクトル検索が完了しました (user_id=%s, hits=%d)。", user_id, len(result)
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(
                "層3ベクトル検索が %.1f秒でタイムアウトしたため similar_snippets を空に"
                "フォールバックします (user_id=%s, client=%s)。層1/層2の制約と生成は継続します。",
                timeout_sec,
                user_id,
                type(self.vector_search_client).__name__,
            )
            return []
        except BaseException:
            # 層3（好み学習＝加点要素）の失敗は提案の成立を妨げない。
            # 真の例外は warning + traceback で必ず記録した上で空リストにフォールバックする。
            # CancelledError も含めて隔離するが、CancelledError の場合は現タスクの
            # キャンセル要求を尊重して再送出する（フォールバックで飲み込むとタスク
            # キャンセルが効かなくなるため）。
            if isinstance(sys.exc_info()[1], asyncio.CancelledError):
                logger.warning(
                    "層3ベクトル検索がキャンセルされました (user_id=%s)。再送出します。",
                    user_id,
                )
                raise
            logger.warning(
                "層3ベクトル検索に失敗したため similar_snippets を空にフォールバックします "
                "(user_id=%s, client=%s)。層1/層2の制約と生成は継続します。",
                user_id,
                type(self.vector_search_client).__name__,
                exc_info=True,
            )
            return []

    # ---- 統合エントリポイント -------------------------------------------

    async def retrieve(
        self,
        user_id: str,
        query_text: str = "",
        top_k: int = 3,
    ) -> RetrievedContext:
        """
        層1・層2・層3を1回の呼び出しで統合取得する。
        Vision Analyzer Agent と `asyncio.gather` 等で並列実行可能。
        """
        user = get_user(user_id)
        if user is None:
            raise ValueError(f"ユーザーが見つかりません: {user_id}")

        hard_constraints = self._get_hard_constraints(user)
        structured_feedback = self._get_structured_feedback(user_id)

        # InMemoryVectorSearchClient を使っている場合はDBの自由記述FBをコーパスとしてシードする。
        # (AlloyDB(pgvector) 実装に差し替えた場合はDB書き込み側で対応するため不要)
        # 注意: 層3'（外部レシピソース）はIssue #78の方針転換によりベクトル検索コーパスに
        # 混入させない。全件は _get_favorite_recipe_sources で別経路取得する。
        if isinstance(self.vector_search_client, InMemoryVectorSearchClient):
            db_snippets = self._load_free_text_corpus_from_db(user_id)
            existing_ids = {s.id for s in self.vector_search_client.corpus}
            for snippet in db_snippets:
                if snippet.id not in existing_ids:
                    self.vector_search_client.corpus.append(snippet)

        similar_snippets = await self._get_similar_snippets(
            user_id=user_id,
            query_text=query_text,
            negative_tags=structured_feedback.negative_tags,
            top_k=top_k,
        )
        recent_proposal_titles = self._get_recent_proposal_titles(user_id)
        health_data = await self.health_data_client.get_yesterday_health_data()
        favorite_recipe_sources = self._get_favorite_recipe_sources(user_id)

        return RetrievedContext(
            user_id=user_id,
            hard_constraints=hard_constraints,
            structured_feedback=structured_feedback,
            similar_snippets=similar_snippets,
            recent_proposal_titles=recent_proposal_titles,
            health_data=health_data,
            favorite_recipe_sources=favorite_recipe_sources,
        )
