"""
Issue #76: 層1/層2 構造化データストアのユニットテスト（Firestore 実装）

- FirestoreStructuredStore が層1（ハード制約）を決定的フィルタで取得すること
- FirestoreStructuredStore が層2（構造化FB）の negative/positive_tags を集約すること
- ContextRetrieverAgent が structured_store 経由で取得できること
"""
from unittest.mock import MagicMock

import pytest

from app.agents.context_retriever import ContextRetrieverAgent
from app.agents.structured_store import (
    FirestoreStructuredStore,
    HardConstraintsData,
    StructuredFeedbackData,
)


def test_firestore_store_hard_constraints_is_deterministic(mock_firestore):
    """層1はFirestoreから決定的に読み出されること"""
    mock_firestore.add_user(
        uid="store-user-001",
        email="store-user-001@example.com",
        preferences={
            "allergies": ["卵"],
            "dislikes": ["ナス"],
            "kitchen_tools": ["電子レンジ"],
        },
    )
    store = FirestoreStructuredStore()
    result = store.get_hard_constraints("store-user-001")
    assert result.allergies == ["卵"]
    assert result.forbidden_ingredients == ["ナス"]
    assert result.available_kitchen_tools == ["電子レンジ"]


def test_firestore_store_structured_feedback_aggregates_tags(mock_firestore):
    mock_firestore.add_user(uid="store-user-002", email="store-user-002@example.com")
    mock_firestore.add_feedback(
        user_id="store-user-002", id="fb-1", recipe_id="r1",
        feedback_type="reject", tags=["揚げ物"],
    )
    mock_firestore.add_feedback(
        user_id="store-user-002", id="fb-2", recipe_id="r2",
        feedback_type="cooked", tags=["味付けが最高"], rating=5,
    )

    store = FirestoreStructuredStore()
    result = store.get_structured_feedback("store-user-002")
    assert result.negative_tags == ["揚げ物"]
    assert result.positive_tags == ["味付けが最高"]


def test_context_retriever_uses_injected_structured_store(mock_firestore):
    """ContextRetrieverAgent が structured_store 経由で層1/2を取得すること"""
    mock_firestore.add_user(uid="store-user-003", email="store-user-003@example.com")
    mock_store = MagicMock()
    mock_store.get_hard_constraints.return_value = HardConstraintsData(
        allergies=["えび"], forbidden_ingredients=[], available_kitchen_tools=[]
    )
    mock_store.get_structured_feedback.return_value = StructuredFeedbackData(
        negative_tags=["辛い"], positive_tags=[]
    )

    agent = ContextRetrieverAgent(structured_store=mock_store)
    from app.firestore_store import UserDoc
    user = UserDoc(mock_firestore.users["store-user-003"])
    hard_constraints = agent._get_hard_constraints(user)
    structured_feedback = agent._get_structured_feedback(user.uid)

    assert hard_constraints.allergies == ["えび"]
    assert structured_feedback.negative_tags == ["辛い"]
    mock_store.get_hard_constraints.assert_called_once_with(user.uid)
    mock_store.get_structured_feedback.assert_called_once_with(user.uid)
