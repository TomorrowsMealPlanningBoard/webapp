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


# -----------------------------------------------------------------------
# 回帰テスト: mood_tags 指定時の /api/propose 500 (output_context KeyError)
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_run_survives_vector_search_failure(mock_firestore):
    """
    層3ベクトル検索(本番は Memory Bank)が例外を投げても orchestrator.run が
    例外を伝播（=/api/propose 500）せず、提案(meal_plan)を返して成立すること。

    従来: collect_context の例外を ADK 並列ランナーが握り潰し output_context が
    未設定 → generate ノードで KeyError: 'output_context' → main.py が
    "提案の生成に失敗しました: 'output_context'" を 500 で返していた。

    本テストは ContextRetrieverAgent.retrieve をモックせず**実物**を走らせ、
    その内部で使うベクトル検索クライアントだけを失敗させることで、
    context_retriever のフォールバックが orchestrator 経由でも効くことを検証する。
    Gemini(生成)・Reviewer は従来通りモック。
    """
    mock_firestore.add_user(
        uid="test_user_fb", email="test_fb@example.com",
        preferences={"allergies": ["卵"], "dislikes": [], "goal": "none", "kitchen_tools": []},
    )
    # mood_tags を指定（本番で 500 を確定的に再現していた条件）
    req = SuggestRequest(
        cooking_time=30,
        effort_level="normal",
        mood_tags=["肉料理"],
        mood_freetext="",
        ingredients=[],
    )
    mock_meal_plan = _make_meal_plan()

    class FailingVectorSearchClient:
        async def search(self, user_id, query_text, top_k, exclude_tags=()):
            raise RuntimeError("Memory Bank search_memory failed (simulated 500 cause)")

    orchestrator = MealOrchestrator()

    # ContextRetrieverAgent() は __init__ 内で
    # `from .memory_bank_client import build_vector_search_client` を実行するため、
    # 元モジュール属性を差し替えれば実物の retrieve() が失敗クライアントを掴む。
    with (
        patch(
            "app.agents.memory_bank_client.build_vector_search_client",
            return_value=FailingVectorSearchClient(),
        ),
        patch(
            "app.agents.orchestrator.rg.generate_meal_plan",
            return_value=(mock_meal_plan, "テストメッセージ"),
        ),
    ):
        result = await orchestrator.run(user_id="test_user_fb", req=req)

    # 500 にならず提案が成立する
    assert isinstance(result, OrchestratorResult)
    assert result.meal_plan is not None
    # 層3はフォールバックで空
    assert result.context is not None
    assert result.context.similar_snippets == []
    # 層1（アレルギー）は決定的に取得され続ける
    assert set(result.context.hard_constraints.allergies) == {"卵"}


# -----------------------------------------------------------------------
# 回帰テスト（本番失敗様態の再現）:
#   mood_tags 非空 → 層3(本番 Memory Bank)が「遅い」ために、画像なしで即完了する
#   collect_vision が先に generate をトリガーしてしまい output_context 未設定で
#   500 になっていた（#110 の1次修正では直りきらなかった真因）。
#
#   #110 の failing test（上記 test_..._survives_vector_search_failure）は
#   search() を「同期・即時例外」にしていたため collect_context がフォールバックで
#   即完了し、この "遅延レース" を再現できていなかった。ここでは search() を
#   「遅い async」にすることで本番と同じ様態（合流バリア欠如によるスケジューリング競合）
#   を再現する。fix（JoinNode バリア）を外すと落ち、入れると通る。
# -----------------------------------------------------------------------

def _make_ctx_context() -> RetrievedContext:
    """collect_context が返す RetrievedContext（層3は空でよい）。"""
    return RetrievedContext(
        user_id="test_user_slow",
        hard_constraints=HardConstraints(
            allergies=["卵"], forbidden_ingredients=[], available_kitchen_tools=[]
        ),
        structured_feedback=StructuredFeedbackContext(negative_tags=[], positive_tags=[]),
    )


@pytest.mark.asyncio
async def test_orchestrator_run_survives_slow_context_retrieval(mock_firestore):
    """
    本番失敗様態の再現: collect_context が「遅い」場合でも、generate が先走らず
    （＝合流バリアが効いて）500 にならず提案が成立すること。

    ContextRetrieverAgent.retrieve 自体を「遅い async」に差し替える。画像なしのため
    collect_vision は即完了する。バリアが無い（旧配線）と generate が先に走り
    output_context 未設定で RuntimeError（=500）になる。JoinNode バリアがあれば
    generate は両ノードの完了を待つため 200 で成立する。
    """
    mock_firestore.add_user(uid="test_user_slow", email="slow@example.com")
    req = SuggestRequest(
        cooking_time=30, effort_level="normal",
        mood_tags=["肉料理"], mood_freetext="", ingredients=[],
    )
    mock_meal_plan = _make_meal_plan()
    slow_context = _make_ctx_context()

    async def slow_retrieve(*args, **kwargs):
        # 本番 Memory Bank の ~7秒ハングをスケールダウンして再現。
        await asyncio.sleep(0.4)
        return slow_context

    orchestrator = MealOrchestrator()
    with (
        patch(
            "app.agents.orchestrator.ContextRetrieverAgent.retrieve",
            new=AsyncMock(side_effect=slow_retrieve),
        ),
        patch(
            "app.agents.orchestrator.rg.generate_meal_plan",
            return_value=(mock_meal_plan, "テストメッセージ"),
        ),
    ):
        result = await orchestrator.run(user_id="test_user_slow", req=req)

    # 遅延レースがあっても 500 にならず提案が成立する（バリアが効いている）
    assert isinstance(result, OrchestratorResult)
    assert result.meal_plan is not None
    assert result.context is not None
    # collect_context の結果が下流に確実に伝播している
    assert set(result.context.hard_constraints.allergies) == {"卵"}


@pytest.mark.asyncio
async def test_orchestrator_run_survives_slow_and_failing_memory_bank(mock_firestore):
    """
    本番失敗様態のより忠実な再現:
      - ContextRetrieverAgent は実物を走らせる
      - 層3ベクトル検索クライアント（本番 Memory Bank 相当）だけを「遅くて最後に失敗」に差し替え
    これで「Memory Bank が ~7秒ハングした末に落ちる」本番の様態を再現する。
    fix（context_retriever の timeout+BaseException フォールバック ＋ JoinNode バリア）に
    より、mood_tags 非空でも 500 にならず 3案返り、層1（卵）は維持され、層3は空になる。
    """
    mock_firestore.add_user(
        uid="test_user_mb", email="mb@example.com",
        preferences={"allergies": ["卵"], "dislikes": [], "goal": "none", "kitchen_tools": []},
    )
    req = SuggestRequest(
        cooking_time=30, effort_level="normal",
        mood_tags=["肉料理"], mood_freetext="", ingredients=[],
    )
    mock_meal_plan = _make_meal_plan()

    class SlowFailingClient:
        async def search(self, user_id, query_text, top_k, exclude_tags=()):
            await asyncio.sleep(0.4)  # ~7秒ハングをスケールダウン
            raise RuntimeError("Memory Bank search_memory failed after hang (simulated)")

    orchestrator = MealOrchestrator()
    with (
        patch(
            "app.agents.memory_bank_client.build_vector_search_client",
            return_value=SlowFailingClient(),
        ),
        patch(
            "app.agents.orchestrator.rg.generate_meal_plan",
            return_value=(mock_meal_plan, "テストメッセージ"),
        ),
        # 本テストでは層3を短いタイムアウトに縛らず、遅延後の失敗フォールバックを検証
        patch.dict("os.environ", {"VECTOR_SEARCH_TIMEOUT_SEC": "5"}),
    ):
        result = await orchestrator.run(user_id="test_user_mb", req=req)

    assert isinstance(result, OrchestratorResult)
    assert result.meal_plan is not None
    assert result.context is not None
    assert result.context.similar_snippets == []          # 層3は空フォールバック
    assert set(result.context.hard_constraints.allergies) == {"卵"}  # 層1維持


@pytest.mark.asyncio
async def test_layer3_timeout_falls_back_to_empty(mock_firestore):
    """
    goal 4 の検証: 層3ベクトル検索が「タイムアウト時間を超えてハング」しても、
    タイムアウトで打ち切って空フォールバックし提案が成立すること（本番 Memory Bank の
    ~7秒ハング対策）。VECTOR_SEARCH_TIMEOUT_SEC を短く設定して検証する。
    """
    mock_firestore.add_user(
        uid="test_user_to", email="to@example.com",
        preferences={"allergies": ["卵"], "dislikes": [], "goal": "none", "kitchen_tools": []},
    )
    req = SuggestRequest(
        cooking_time=30, effort_level="normal",
        mood_tags=["肉料理"], mood_freetext="", ingredients=[],
    )
    mock_meal_plan = _make_meal_plan()

    class HangingClient:
        async def search(self, user_id, query_text, top_k, exclude_tags=()):
            await asyncio.sleep(10)  # タイムアウト（0.3s）を大きく超えてハング
            return []

    orchestrator = MealOrchestrator()
    with (
        patch(
            "app.agents.memory_bank_client.build_vector_search_client",
            return_value=HangingClient(),
        ),
        patch(
            "app.agents.orchestrator.rg.generate_meal_plan",
            return_value=(mock_meal_plan, "テストメッセージ"),
        ),
        patch.dict("os.environ", {"VECTOR_SEARCH_TIMEOUT_SEC": "0.3"}),
    ):
        result = await orchestrator.run(user_id="test_user_to", req=req)

    assert isinstance(result, OrchestratorResult)
    assert result.meal_plan is not None
    assert result.context is not None
    assert result.context.similar_snippets == []
    assert set(result.context.hard_constraints.allergies) == {"卵"}
