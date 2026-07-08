"""
層1（ハード制約）・層2（構造化FB）の永続化を担う構造化データストア。

Firestore 専一実装。コレクション構成は firestore_store.py のドキュメントを参照。
決定的フィルタの原則: 本モジュールはドキュメント取得と if 文による集約のみを行う。
ベクトル検索・LLM 呼び出しを含んではならない。
"""
from __future__ import annotations

from typing import List, Protocol, runtime_checkable

from ..firestore_store import get_user, get_feedbacks


class HardConstraintsData:
    def __init__(
        self,
        allergies: List[str],
        forbidden_ingredients: List[str],
        available_kitchen_tools: List[str],
    ) -> None:
        self.allergies = allergies
        self.forbidden_ingredients = forbidden_ingredients
        self.available_kitchen_tools = available_kitchen_tools


class StructuredFeedbackData:
    def __init__(self, negative_tags: List[str], positive_tags: List[str]) -> None:
        self.negative_tags = negative_tags
        self.positive_tags = positive_tags


@runtime_checkable
class StructuredStore(Protocol):
    def get_hard_constraints(self, user_id: str) -> HardConstraintsData: ...
    def get_structured_feedback(self, user_id: str) -> StructuredFeedbackData: ...


class FirestoreStructuredStore:
    """
    本番用実装。層1/層2を Firestore の users/{user_id} ドキュメント配下で管理する。
    決定的フィルタの原則: Firestore からのドキュメント取得と if 文による集約のみを行う。
    """

    def get_hard_constraints(self, user_id: str) -> HardConstraintsData:
        user = get_user(user_id)
        if user is None:
            raise ValueError(f"ユーザーが見つかりません: {user_id}")
        prefs = user.preferences or {}
        return HardConstraintsData(
            allergies=list(prefs.get("allergies") or []),
            forbidden_ingredients=list(prefs.get("dislikes") or []),
            available_kitchen_tools=list(prefs.get("kitchen_tools") or []),
        )

    def get_structured_feedback(self, user_id: str) -> StructuredFeedbackData:
        feedbacks = get_feedbacks(user_id)

        negative_tags: set[str] = set()
        positive_tags: set[str] = set()
        for fb in feedbacks:
            if fb.feedback_type == "reject":
                negative_tags.update(fb.tags or [])
            elif fb.feedback_type == "cooked":
                positive_tags.update(fb.tags or [])

        return StructuredFeedbackData(
            negative_tags=sorted(negative_tags),
            positive_tags=sorted(positive_tags),
        )
