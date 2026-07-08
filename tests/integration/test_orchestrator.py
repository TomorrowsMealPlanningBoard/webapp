"""
Integration tests for ADK Orchestrator (#31).
テスト方針: 外部API（Gemini）はモックし、エージェント間の連携・ループ制御ロジックを検証する。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.agents.context_retriever import (
    HardConstraints,
    RetrievedContext,
    StructuredFeedbackContext,
)
from app.agents.orchestrator import MealOrchestrator, OrchestratorResult
from app.main import app
from app.schemas import (
    IngredientItem,
    MealItem,
    MealPlan,
    RecipeStep,
    SuggestRequest,
)


# -----------------------------------------------------------------------
# テスト用フィクスチャ
# -----------------------------------------------------------------------

def _make_meal_item(meal_type: str, title: str) -> MealItem:
    return MealItem(
        id=f"{meal_type}_001",
        meal_type=meal_type,
        title=title,
        emoji="🍳",
        description="テスト用の料理です。",
        cooking_time=20,
        effort_level="normal",
        servings=2,
        tags=["和食"],
        ingredients=["卵 2個", "ご飯 1杯"],
        steps=[RecipeStep(step=1, description="作る")],
        nutrition_note=None,
        required_tools=[],
    )


def _make_meal_plan() -> MealPlan:
    return MealPlan(
        breakfast=_make_meal_item("breakfast", "朝食テスト"),
        lunch=_make_meal_item("lunch", "昼食テスト"),
        dinner=_make_meal_item("dinner", "夕食テスト"),
    )


def _make_context(user_id: str = "test_user") -> RetrievedContext:
    return RetrievedContext(
        user_id=user_id,
        hard_constraints=HardConstraints(
            allergies=[],
            forbidden_ingredients=[],
            available_kitchen_tools=[],
        ),
        structured_feedback=StructuredFeedbackContext(
            negative_tags=[],
            positive_tags=[],
        ),
    )


def _make_req() -> SuggestRequest:
    return SuggestRequest(
        cooking_time=30,
        effort_level="normal",
        mood_tags=["さっぱり"],
        mood_freetext="",
        ingredients=[IngredientItem(name="卵", quantity=2, unit="個", freshness="good")],
    )


# -----------------------------------------------------------------------
# テスト
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_runs_data_collection_in_parallel(mock_firestore):
    mock_firestore.add_user(uid="test_user", email="test@example.com")
    req = _make_req()
    mock_meal_plan = _make_meal_plan()
    mock_context = _make_context()

    orchestrator = MealOrchestrator()

    with (
        patch(
            "app.agents.orchestrator.ContextRetrieverAgent.retrieve",
            new=AsyncMock(return_value=mock_context),
        ),
        patch(
            "app.agents.orchestrator.rg.generate_meal_plan",
            return_value=(mock_meal_plan, "テストメッセージ"),
        ),
    ):
        result = await orchestrator.run(user_id="test_user", req=req)

    assert isinstance(result, OrchestratorResult)
    assert result.meal_plan is not None
    assert "data_collection_ms" in result.phase_durations_ms


@pytest.mark.asyncio
async def test_data_collection_without_image(mock_firestore):
    mock_firestore.add_user(uid="test_user", email="test@example.com")
    req = _make_req()
    mock_context = _make_context()

    orchestrator = MealOrchestrator()

    with patch(
        "app.agents.orchestrator.ContextRetrieverAgent.retrieve",
        new=AsyncMock(return_value=mock_context),
    ):
        context, vision_ingredients = await orchestrator._run_data_collection(
            user_id="test_user",
            req=req,
            image_bytes=None,
        )

    assert context == mock_context
    assert "卵" in vision_ingredients


def test_generation_phase_receives_context(mock_firestore):
    req = _make_req()
    mock_context = _make_context()
    mock_meal_plan = _make_meal_plan()

    orchestrator = MealOrchestrator()

    with patch(
        "app.agents.orchestrator.rg.generate_meal_plan",
        return_value=(mock_meal_plan, "メッセージ"),
    ) as mock_gen:
        plan, msg = orchestrator._run_generation(req, mock_context)

    mock_gen.assert_called_once_with(req, mock_context)
    assert plan == mock_meal_plan
    assert msg == "メッセージ"


def test_review_loop_retries_on_violation(mock_firestore):
    req = _make_req()

    context = RetrievedContext(
        user_id="test_user",
        hard_constraints=HardConstraints(
            allergies=["卵"],
            forbidden_ingredients=[],
            available_kitchen_tools=[],
        ),
        structured_feedback=StructuredFeedbackContext(negative_tags=[], positive_tags=[]),
    )

    violation_item = MealItem(
        id="breakfast_001",
        meal_type="breakfast",
        title="卵かけご飯",
        emoji="🍳",
        description="卵を使った料理",
        cooking_time=10,
        effort_level="easy",
        servings=1,
        tags=["卵料理"],
        ingredients=["卵 1個", "ご飯 1杯"],
        steps=[RecipeStep(step=1, description="作る")],
        nutrition_note=None,
        required_tools=[],
    )
    clean_item = MealItem(
        id="breakfast_002",
        meal_type="breakfast",
        title="トーストとスープ",
        emoji="🍞",
        description="シンプルな朝食です",
        cooking_time=10,
        effort_level="easy",
        servings=1,
        tags=["洋食"],
        ingredients=["食パン 1枚", "スープ 1杯"],
        steps=[RecipeStep(step=1, description="作る")],
        nutrition_note=None,
        required_tools=[],
    )

    initial_plan = MealPlan(
        breakfast=violation_item,
        lunch=_make_meal_item("lunch", "昼食"),
        dinner=_make_meal_item("dinner", "夕食"),
    )

    regen_call_count = [0]

    def mock_generate(r, c):
        regen_call_count[0] += 1
        clean_plan = MealPlan(
            breakfast=clean_item,
            lunch=_make_meal_item("lunch", "昼食"),
            dinner=_make_meal_item("dinner", "夕食"),
        )
        return clean_plan, "再生成メッセージ"

    orchestrator = MealOrchestrator()

    with patch("app.agents.orchestrator.rg.generate_meal_plan", side_effect=mock_generate):
        reviewed_plan, retry_counts = orchestrator._run_review_loop(initial_plan, context, req)

    assert regen_call_count[0] >= 1
    assert reviewed_plan.breakfast.title == "トーストとスープ"
    assert len(retry_counts) == 3


@pytest.mark.asyncio
async def test_all_agents_run_in_same_process(mock_firestore):
    mock_firestore.add_user(uid="test_user", email="test@example.com")
    req = _make_req()
    mock_meal_plan = _make_meal_plan()
    mock_context = _make_context()

    orchestrator = MealOrchestrator()

    with (
        patch(
            "app.agents.orchestrator.ContextRetrieverAgent.retrieve",
            new=AsyncMock(return_value=mock_context),
        ),
        patch(
            "app.agents.orchestrator.rg.generate_meal_plan",
            return_value=(mock_meal_plan, "OK"),
        ),
    ):
        result = await orchestrator.run(user_id="test_user", req=req)

    assert "data_collection_ms" in result.phase_durations_ms
    assert "generation_ms" in result.phase_durations_ms
    assert "review_ms" in result.phase_durations_ms


@pytest.mark.asyncio
async def test_phase_durations_are_recorded(mock_firestore):
    mock_firestore.add_user(uid="test_user", email="test@example.com")
    req = _make_req()
    mock_meal_plan = _make_meal_plan()
    mock_context = _make_context()

    orchestrator = MealOrchestrator()

    with (
        patch(
            "app.agents.orchestrator.ContextRetrieverAgent.retrieve",
            new=AsyncMock(return_value=mock_context),
        ),
        patch(
            "app.agents.orchestrator.rg.generate_meal_plan",
            return_value=(mock_meal_plan, "OK"),
        ),
    ):
        result = await orchestrator.run(user_id="test_user", req=req)

    for key in ["data_collection_ms", "generation_ms", "review_ms"]:
        assert key in result.phase_durations_ms
        assert result.phase_durations_ms[key] >= 0

    assert len(result.reviewer_retries) == 3
