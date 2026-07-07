"""
Issue #76: 層1/層2 構造化データストアの選定と ContextRetriever 接続切り替え

- `USE_FIRESTORE` 未設定時は既存の SQLAlchemy 経由（SQLite/AlloyDB共存構成）で動作すること
- `USE_FIRESTORE=true` 時は FirestoreStructuredStore が選択されること
- 層1（ハード制約）が決定的フィルタ（if文）のみで構築され、ベクトル検索を経由しないこと
- ContextRetrieverAgent が新ストア経由で `_get_hard_constraints` / `_get_structured_feedback`
  を動作させられること（`structured_store` の差し替えで検証）
"""
from unittest.mock import MagicMock

import pytest

from app.agents.context_retriever import ContextRetrieverAgent
from app.agents.structured_store import (
    FirestoreStructuredStore,
    HardConstraintsData,
    SqlAlchemyStructuredStore,
    StructuredFeedbackData,
    build_structured_store,
)
from app.models import Feedback, User


def _make_user(db, uid="store-user-001"):
    user = User(
        uid=uid,
        email=f"{uid}@example.com",
        hashed_password=None,
        display_name="ストアテストユーザー",
        preferences={
            "allergies": ["卵"],
            "dislikes": ["ナス"],
            "kitchen_tools": ["電子レンジ"],
        },
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_build_structured_store_defaults_to_sqlalchemy(db, monkeypatch):
    monkeypatch.delenv("USE_FIRESTORE", raising=False)
    store = build_structured_store(db)
    assert isinstance(store, SqlAlchemyStructuredStore)


def test_build_structured_store_switches_to_firestore(db, monkeypatch):
    monkeypatch.setenv("USE_FIRESTORE", "true")
    monkeypatch.setattr(
        "app.agents.structured_store.FirestoreStructuredStore.__init__",
        lambda self, project=None: None,
    )
    store = build_structured_store(db)
    assert isinstance(store, FirestoreStructuredStore)


def test_sqlalchemy_store_hard_constraints_is_deterministic(db):
    """層1はif文による機械的な読み出しのみで構築され、ベクトル検索を経由しないこと"""
    user = _make_user(db)
    store = SqlAlchemyStructuredStore(db=db)
    result = store.get_hard_constraints(user.uid)
    assert result.allergies == ["卵"]
    assert result.forbidden_ingredients == ["ナス"]
    assert result.available_kitchen_tools == ["電子レンジ"]


def test_sqlalchemy_store_structured_feedback_aggregates_tags(db):
    user = _make_user(db)
    db.add(Feedback(
        id="fb-1", user_id=user.uid, recipe_id="r1", feedback_type="reject", tags=["揚げ物"],
    ))
    db.add(Feedback(
        id="fb-2", user_id=user.uid, recipe_id="r2", feedback_type="cooked",
        tags=["味付けが最高"], rating=5,
    ))
    db.commit()

    store = SqlAlchemyStructuredStore(db=db)
    result = store.get_structured_feedback(user.uid)
    assert result.negative_tags == ["揚げ物"]
    assert result.positive_tags == ["味付けが最高"]


def test_context_retriever_uses_injected_structured_store(db):
    """ContextRetrieverAgent が structured_store 経由で層1/2を取得すること"""
    user = _make_user(db)
    mock_store = MagicMock()
    mock_store.get_hard_constraints.return_value = HardConstraintsData(
        allergies=["えび"], forbidden_ingredients=[], available_kitchen_tools=[]
    )
    mock_store.get_structured_feedback.return_value = StructuredFeedbackData(
        negative_tags=["辛い"], positive_tags=[]
    )

    agent = ContextRetrieverAgent(db=db, structured_store=mock_store)
    hard_constraints = agent._get_hard_constraints(user)
    structured_feedback = agent._get_structured_feedback(user.uid)

    assert hard_constraints.allergies == ["えび"]
    assert structured_feedback.negative_tags == ["辛い"]
    mock_store.get_hard_constraints.assert_called_once_with(user.uid)
    mock_store.get_structured_feedback.assert_called_once_with(user.uid)


@pytest.mark.asyncio
async def test_context_retriever_retrieve_end_to_end_with_default_store(db):
    """USE_FIRESTORE未指定時、retrieve() が従来通りSQLite経由で動作すること（共存構成の回帰確認）"""
    user = _make_user(db)
    db.add(Feedback(
        id="fb-neg", user_id=user.uid, recipe_id="r1", feedback_type="reject", tags=["辛い"],
    ))
    db.commit()

    agent = ContextRetrieverAgent(db=db)
    context = await agent.retrieve(user_id=user.uid)

    assert context.hard_constraints.allergies == ["卵"]
    assert context.structured_feedback.negative_tags == ["辛い"]
