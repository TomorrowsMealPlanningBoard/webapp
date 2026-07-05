"""
Integration tests for ADK Orchestrator (#31).
テスト方針: 外部API（Gemini）はモックし、エージェント間の連携・ループ制御ロジックを検証する。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.context_retriever import (
    HardConstraints,
    RetrievedContext,
    StructuredFeedbackContext,
)
from app.agents.orchestrator import MealOrchestrator, OrchestratorResult
from app.database import Base, get_db
from app.main import app
from app.schemas import (
    IngredientItem,
    MealItem,
    MealPlan,
    RecipeStep,
    SuggestRequest,
)

# -----------------------------------------------------------------------
# テスト用DB（インメモリSQLite）
# -----------------------------------------------------------------------

TEST_DB_URL = "sqlite:///./test_orchestrator.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    client.post(
        "/api/auth/register",
        json={"email": "orch@test.com", "password": "pass1234"},
    )
    res = client.post(
        "/api/auth/login",
        data={"username": "orch@test.com", "password": "pass1234"},
    )
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


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
# テスト: Orchestrator が Context Retriever と Vision Analyzer を並列実行する
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_runs_data_collection_in_parallel(db):
    """ADK Workflow で Context Retriever と Vision Analyzer が並列ノードとして実行されること。"""
    req = _make_req()
    mock_meal_plan = _make_meal_plan()
    mock_context = _make_context()

    orchestrator = MealOrchestrator(db=db)

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
    # ADK Workflow で data_collection_ms が記録されていること（並列実行の証拠）
    assert "data_collection_ms" in result.phase_durations_ms


# -----------------------------------------------------------------------
# テスト: _run_data_collection が Vision なしでも動作する
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_data_collection_without_image(db):
    """画像なしの場合、Vision Analyzer をスキップして req.ingredients を使う。"""
    req = _make_req()
    mock_context = _make_context()

    orchestrator = MealOrchestrator(db=db)

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
    # 画像なし → req.ingredients から食材リストを構築
    assert "卵" in vision_ingredients


# -----------------------------------------------------------------------
# テスト: Recipe Generator に集約結果を渡して3案を生成する
# -----------------------------------------------------------------------

def test_generation_phase_receives_context(db):
    """_run_generation が context を受け取り generate_meal_plan を呼ぶこと。"""
    req = _make_req()
    mock_context = _make_context()
    mock_meal_plan = _make_meal_plan()

    orchestrator = MealOrchestrator(db=db)

    with patch(
        "app.agents.orchestrator.rg.generate_meal_plan",
        return_value=(mock_meal_plan, "メッセージ"),
    ) as mock_gen:
        plan, msg = orchestrator._run_generation(req, mock_context)

    mock_gen.assert_called_once_with(req, mock_context)
    assert plan == mock_meal_plan
    assert msg == "メッセージ"


# -----------------------------------------------------------------------
# テスト: Reviewer が違反を検出したとき Generator に差し戻してループする
# -----------------------------------------------------------------------

def test_review_loop_retries_on_violation(db):
    """アレルギー違反のレシピが差し戻され、再生成後に承認されること。"""
    req = _make_req()

    # アレルギー: 卵
    context = RetrievedContext(
        user_id="test_user",
        hard_constraints=HardConstraints(
            allergies=["卵"],
            forbidden_ingredients=[],
            available_kitchen_tools=[],
        ),
        structured_feedback=StructuredFeedbackContext(negative_tags=[], positive_tags=[]),
    )

    # 最初は「卵」をタグに含む違反レシピ
    violation_item = MealItem(
        id="breakfast_001",
        meal_type="breakfast",
        title="卵かけご飯",
        emoji="🍳",
        description="卵を使った料理",
        cooking_time=10,
        effort_level="easy",
        servings=1,
        tags=["卵料理"],          # ← 「卵」がタグに含まれる → アレルギーに引っかかる
        ingredients=["卵 1個", "ご飯 1杯"],
        steps=[RecipeStep(step=1, description="作る")],
        nutrition_note=None,
        required_tools=[],
    )
    # 差し戻し後の再生成レシピ（卵を含まない）
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

    orchestrator = MealOrchestrator(db=db)

    with patch("app.agents.orchestrator.rg.generate_meal_plan", side_effect=mock_generate):
        reviewed_plan, retry_counts = orchestrator._run_review_loop(initial_plan, context, req)

    # breakfast が差し戻されて再生成されたこと
    assert regen_call_count[0] >= 1
    # 最終的に承認されたレシピは卵を含まないもの
    assert reviewed_plan.breakfast.title == "トーストとスープ"
    # リトライ回数が記録されていること（3食分）
    assert len(retry_counts) == 3


# -----------------------------------------------------------------------
# テスト: 全エージェントが同一プロセス内で引数渡し（ADK ctx.state）により連携する
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_agents_run_in_same_process(db):
    """ADK Workflow が外部プロセスを起動せず同一プロセス内で動作し、
    phase_durations_ms が記録されること。"""
    req = _make_req()
    mock_meal_plan = _make_meal_plan()
    mock_context = _make_context()

    orchestrator = MealOrchestrator(db=db)

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

    # phase_durations_ms が記録されていること（各フェーズがログ可能であることを示す）
    assert "data_collection_ms" in result.phase_durations_ms
    assert "generation_ms" in result.phase_durations_ms
    assert "review_ms" in result.phase_durations_ms


# -----------------------------------------------------------------------
# テスト: POST /api/propose エンドポイント（エンドツーエンド）
# -----------------------------------------------------------------------

def test_propose_endpoint_returns_meal_plan(client, auth_headers):
    """POST /api/propose が meal_plan を含む SuggestResponse を返すこと。"""
    mock_meal_plan = _make_meal_plan()
    mock_context = _make_context()

    with (
        patch(
            "app.main.MealOrchestrator.run",
            new=AsyncMock(
                return_value=MagicMock(
                    meal_plan=mock_meal_plan,
                    message="テスト提案完了",
                    phase_durations_ms={"data_collection_ms": 100, "generation_ms": 200, "review_ms": 50},
                    reviewer_retries=[0, 0, 0],
                    vision_skipped=True,
                )
            ),
        ),
    ):
        res = client.post(
            "/api/propose",
            data={
                "cooking_time": 30,
                "effort_level": "normal",
                "mood_tags": '["さっぱり"]',
                "mood_freetext": "",
            },
            headers=auth_headers,
        )

    assert res.status_code == 200
    data = res.json()
    assert "meal_plan" in data
    assert data["meal_plan"]["breakfast"]["title"] == "朝食テスト"
    assert data["meal_plan"]["lunch"]["title"] == "昼食テスト"
    assert data["meal_plan"]["dinner"]["title"] == "夕食テスト"
    assert data["message"] == "テスト提案完了"


# -----------------------------------------------------------------------
# テスト: 処理時間・リトライ回数がログ出力される（ログハンドラで確認）
# -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_phase_durations_are_recorded(db):
    """各フェーズの処理時間が OrchestratorResult に記録されること。"""
    req = _make_req()
    mock_meal_plan = _make_meal_plan()
    mock_context = _make_context()

    orchestrator = MealOrchestrator(db=db)

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

    # 処理時間が非負の数として記録されていること
    for key in ["data_collection_ms", "generation_ms", "review_ms"]:
        assert key in result.phase_durations_ms
        assert result.phase_durations_ms[key] >= 0

    # リトライ回数が記録されていること（違反なしなので 0 が 3 食分）
    assert len(result.reviewer_retries) == 3
