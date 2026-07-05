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

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..models import Feedback, MealProposal, User


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

    def __init__(self, db: Session, vector_search_client: Optional[VectorSearchClient] = None):
        self.db = db
        self.vector_search_client = vector_search_client or InMemoryVectorSearchClient()

    # ---- 層1: 決定的フィルタ（ハード制約） -----------------------------

    def _get_hard_constraints(self, user: User) -> HardConstraints:
        """
        層1: 静的プロファイル/ハード制約を構築する。
        if文による機械的な読み出しのみで、ベクトル検索・確率的処理は一切行わない。
        """
        prefs = user.preferences or {}
        allergies = prefs.get("allergies") or []
        dislikes = prefs.get("dislikes") or []
        kitchen_tools = prefs.get("kitchen_tools") or []

        return HardConstraints(
            allergies=list(allergies),
            forbidden_ingredients=list(dislikes),
            available_kitchen_tools=list(kitchen_tools),
        )

    # ---- 層2: 構造化FB（メタデータ） -----------------------------------

    def _get_structured_feedback(self, user_id: str) -> StructuredFeedbackContext:
        """
        層2: negative_tags / positive_tags を集約する。
        negative_tags は後続のベクトル検索でハードフィルタとして使われる。
        """
        feedbacks = (
            self.db.query(Feedback).filter(Feedback.user_id == user_id).all()
        )

        negative_tags: set[str] = set()
        positive_tags: set[str] = set()
        for fb in feedbacks:
            if fb.feedback_type == "reject":
                negative_tags.update(fb.tags or [])
            elif fb.feedback_type == "cooked":
                positive_tags.update(fb.tags or [])

        return StructuredFeedbackContext(
            negative_tags=sorted(negative_tags),
            positive_tags=sorted(positive_tags),
        )

    # ---- 直近提案履歴取得（Issue #24） ------------------------------------

    def _get_recent_proposal_titles(self, user_id: str, days: int = 7) -> List[str]:
        """
        直近 `days` 日以内に提案済みのレシピタイトル一覧を返す。
        Recipe Generator Agent のプロンプトに注入することで同一レシピの重複提案を防ぐ。
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        proposals = (
            self.db.query(MealProposal)
            .filter(
                MealProposal.user_id == user_id,
                MealProposal.proposed_at >= cutoff,
            )
            .all()
        )
        return [p.recipe_title for p in proposals]

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
        """
        if not query_text:
            return []
        return await self.vector_search_client.search(
            user_id=user_id,
            query_text=query_text,
            top_k=top_k,
            exclude_tags=negative_tags,
        )

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
        user = self.db.query(User).filter(User.uid == user_id).first()
        if user is None:
            raise ValueError(f"ユーザーが見つかりません: {user_id}")

        hard_constraints = self._get_hard_constraints(user)
        structured_feedback = self._get_structured_feedback(user_id)
        similar_snippets = await self._get_similar_snippets(
            user_id=user_id,
            query_text=query_text,
            negative_tags=structured_feedback.negative_tags,
            top_k=top_k,
        )
        recent_proposal_titles = self._get_recent_proposal_titles(user_id)

        return RetrievedContext(
            user_id=user_id,
            hard_constraints=hard_constraints,
            structured_feedback=structured_feedback,
            similar_snippets=similar_snippets,
            recent_proposal_titles=recent_proposal_titles,
        )
