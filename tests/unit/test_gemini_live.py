"""
Voice Cooking Session Agent（Gemini Live / Issue #39）のユニットテスト。

方針:
- 実際の Gemini Live API 音声セッションはモックし、以下を重点的にテストする:
  1. 層1（アレルギー・禁止食材・調理器具）のハード制約が代替食材提案でも守られること
     （既存の Recipe Reviewer Agent の check_recipe / ReviewProfile を再利用していること）
  2. 現在の献立コンテキスト（meal_plan）を踏まえた代替候補が返ること
  3. Live API 接続・送受信に失敗した場合に VoiceSessionUnavailableError を送出し、
     コア献立提案自体には影響しないフォールバック設計になっていること
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.reviewer import ReviewProfile
from app.agents.voice_session import (
    MealPlanContext,
    VoiceCookingSession,
    VoiceSessionUnavailableError,
    ask_cooking_assistant,
    build_fallback_response,
    get_live_model_name,
    suggest_substitute_ingredient,
)
from app.schemas import MealItem, MealPlan, Recipe, RecipeStep


def _make_recipe(ingredients: list[str], required_tools: list[str] | None = None) -> Recipe:
    return Recipe(
        id="recipe_1",
        title="玉ねぎと鶏肉の炒め物",
        emoji="🍳",
        description="テスト用レシピ",
        cooking_time=20,
        effort_level="normal",
        servings=2,
        tags=["和食"],
        ingredients=ingredients,
        steps=[RecipeStep(step=1, description="切る"), RecipeStep(step=2, description="炒める")],
        required_tools=required_tools or [],
    )


def _make_meal_plan(ingredients: list[str]) -> MealPlan:
    recipe = _make_recipe(ingredients)
    item = MealItem(**recipe.model_dump(), meal_type="dinner")
    return MealPlan(breakfast=item, lunch=item, dinner=item)


# ============================================================
# 層1フィルタが代替食材提案でも適用されることのテスト
# ============================================================

class TestSubstituteIngredientLayer1:
    def test_returns_candidates_when_no_constraints(self):
        """制約が無い場合、玉ねぎの代替候補が返ること。"""
        context = MealPlanContext(
            meal_plan=_make_meal_plan(["玉ねぎ 1個", "鶏むね肉 200g"]),
            review_profile=ReviewProfile(),
        )
        result = suggest_substitute_ingredient("玉ねぎ", context)
        assert not result.blocked
        assert len(result.candidates) > 0
        assert "長ねぎ" in result.candidates

    def test_blocks_candidate_that_matches_allergy(self):
        """候補がアレルギー物質に該当する場合、その候補は除外されること（層1）。"""
        # "生姜" を含む候補を持つ "にんにく" のケースで、生姜アレルギーを設定
        context = MealPlanContext(
            meal_plan=_make_meal_plan(["にんにく 1片"]),
            review_profile=ReviewProfile(allergies=["生姜"]),
        )
        result = suggest_substitute_ingredient("にんにく", context)
        # 生姜を含む候補は弾かれているはず
        assert "生姜" not in result.candidates
        for candidate, reasons in result.rejected_candidates:
            if candidate == "生姜":
                assert any("生姜" in r for r in reasons)

    def test_blocks_all_candidates_when_all_violate_constraints(self):
        """全候補が制約に違反する場合、blocked=True で安全側に倒れること。"""
        context = MealPlanContext(
            meal_plan=_make_meal_plan(["牛乳 200ml"]),
            review_profile=ReviewProfile(allergies=["豆乳", "アーモンド", "水"]),
        )
        result = suggest_substitute_ingredient("牛乳", context)
        assert result.blocked is True
        assert result.candidates == []
        assert "提案できませんでした" in result.message or "できません" in result.message

    def test_blocks_candidate_using_missing_kitchen_tool(self):
        """調理器具の制約（層1）も代替食材提案チェックに反映されること。

        candidate の追加自体は required_tools を変えないため、ベースレシピの
        required_tools が所持器具に無い場合は既存材料の代替でも弾かれることを確認する。
        """
        context = MealPlanContext(
            meal_plan=MealPlan(
                breakfast=MealItem(
                    **_make_recipe(["玉ねぎ 1個"], required_tools=["オーブン"]).model_dump(),
                    meal_type="breakfast",
                ),
                lunch=MealItem(
                    **_make_recipe(["玉ねぎ 1個"], required_tools=["オーブン"]).model_dump(),
                    meal_type="lunch",
                ),
                dinner=MealItem(
                    **_make_recipe(["玉ねぎ 1個"], required_tools=["オーブン"]).model_dump(),
                    meal_type="dinner",
                ),
            ),
            review_profile=ReviewProfile(kitchen_tools=["frying_pan"]),  # オーブン未所持
        )
        result = suggest_substitute_ingredient("玉ねぎ", context)
        assert result.blocked is True
        assert result.candidates == []

    def test_no_candidate_pool_for_unknown_ingredient(self):
        """候補プールに無い食材は blocked=True で安全側に倒れること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["キャベツ"]))
        result = suggest_substitute_ingredient("キャベツ", context)
        assert result.blocked is True
        assert result.candidates == []

    def test_negative_tag_constraint_applies(self):
        """層2の negative_tags もチェックに反映されること（reviewerのReviewProfile経由）。"""
        context = MealPlanContext(
            meal_plan=_make_meal_plan(["醤油 大さじ1"]),
            review_profile=ReviewProfile(negative_tags=["味噌"]),
        )
        result = suggest_substitute_ingredient("醤油", context)
        assert "味噌" not in result.candidates


# ============================================================
# Live API 呼び出し（モック）のテスト
# ============================================================

class FakeAsyncSession:
    """google.genai の AsyncSession を模した最小限のフェイク。"""

    def __init__(self, messages):
        self._messages = messages
        self.sent_tool_responses = []

    async def send_client_content(self, *, turns, turn_complete):
        self.sent_turns = turns
        self.sent_turn_complete = turn_complete

    async def send_tool_response(self, *, function_responses):
        self.sent_tool_responses.append(function_responses)

    async def receive(self):
        for m in self._messages:
            yield m


class FakeLiveConnectCM:
    """`client.aio.live.connect(...)` の async context manager を模す。"""

    def __init__(self, session: FakeAsyncSession):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _make_text_message(text: str, turn_complete: bool = True):
    part = MagicMock()
    part.text = text
    model_turn = MagicMock()
    model_turn.parts = [part]
    server_content = MagicMock()
    server_content.model_turn = model_turn
    server_content.turn_complete = turn_complete
    message = MagicMock()
    message.tool_call = None
    message.server_content = server_content
    return message


@pytest.mark.asyncio
class TestVoiceCookingSessionAskViaLiveApi:
    async def test_ask_returns_text_response(self):
        """Live APIからのテキスト応答をそのまま返すこと。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        session = VoiceCookingSession(context=context)

        fake_session = FakeAsyncSession([_make_text_message("次は玉ねぎを炒めてください。")])

        with patch("app.agents.voice_session._get_client") as mock_get_client, \
             patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            mock_client = MagicMock()
            mock_client.aio.live.connect.return_value = FakeLiveConnectCM(fake_session)
            mock_get_client.return_value = mock_client

            answer = await session.ask("次どうする？")

        assert "炒めて" in answer

    async def test_ask_dispatches_function_call_and_uses_layer1_result(self):
        """関数呼び出し（代替食材提案）が発生した場合、層1フィルタ済みの結果を
        send_tool_response に渡し、最終応答テキストを返すこと。"""
        context = MealPlanContext(
            meal_plan=_make_meal_plan(["玉ねぎ 1個"]),
            review_profile=ReviewProfile(allergies=["長ねぎ"]),  # 候補の一部を弾く
        )
        session = VoiceCookingSession(context=context)

        fc = MagicMock()
        fc.id = "call_1"
        fc.name = "suggest_substitute_ingredient"
        fc.args = {"missing_ingredient": "玉ねぎ"}
        tool_call = MagicMock()
        tool_call.function_calls = [fc]
        tool_call_message = MagicMock()
        tool_call_message.tool_call = tool_call
        tool_call_message.server_content = None

        final_message = _make_text_message("玉ねぎの代わりにエシャロットか生姜が使えます。")

        fake_session = FakeAsyncSession([tool_call_message, final_message])

        with patch("app.agents.voice_session._get_client") as mock_get_client, \
             patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            mock_client = MagicMock()
            mock_client.aio.live.connect.return_value = FakeLiveConnectCM(fake_session)
            mock_get_client.return_value = mock_client

            answer = await session.ask("玉ねぎ切らした、代わりある？")

        assert len(fake_session.sent_tool_responses) == 1
        sent = fake_session.sent_tool_responses[0][0]
        # 層1フィルタ（アレルギー: 長ねぎ）により候補から除外されていること
        assert "長ねぎ" not in sent.response["result"]["candidates"]
        assert "エシャロット" in answer or "生姜" in answer

    async def test_ask_raises_unavailable_error_on_connection_failure(self):
        """Live API接続時に例外が発生した場合、VoiceSessionUnavailableErrorに変換されること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        session = VoiceCookingSession(context=context)

        with patch("app.agents.voice_session._get_client") as mock_get_client, \
             patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            mock_client = MagicMock()
            mock_client.aio.live.connect.side_effect = ConnectionError("network down")
            mock_get_client.return_value = mock_client

            with pytest.raises(VoiceSessionUnavailableError):
                await session.ask("次どうする？")

    async def test_ask_raises_unavailable_error_when_project_env_missing(self):
        """GOOGLE_CLOUD_PROJECT未設定でも例外を握ってVoiceSessionUnavailableErrorに変換されること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        session = VoiceCookingSession(context=context)

        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
            with pytest.raises(VoiceSessionUnavailableError):
                await session.ask("次どうする？")

    async def test_ask_raises_unavailable_error_on_empty_response(self):
        """Live APIが空応答を返した場合もフォールバック対象の例外になること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        session = VoiceCookingSession(context=context)

        fake_session = FakeAsyncSession([])  # 何も返さない

        with patch("app.agents.voice_session._get_client") as mock_get_client, \
             patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            mock_client = MagicMock()
            mock_client.aio.live.connect.return_value = FakeLiveConnectCM(fake_session)
            mock_get_client.return_value = mock_client

            with pytest.raises(VoiceSessionUnavailableError):
                await session.ask("次どうする？")


@pytest.mark.asyncio
async def test_ask_cooking_assistant_entrypoint_delegates_to_session():
    """ask_cooking_assistant がVoiceCookingSession.askに委譲すること。"""
    context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))

    with patch.object(VoiceCookingSession, "ask", new=AsyncMock(return_value="OK")) as mock_ask:
        result = await ask_cooking_assistant("次どうする？", context)

    assert result == "OK"
    mock_ask.assert_awaited_once_with("次どうする？")


# ============================================================
# フォールバック（Live API未対応環境でもコア機能が動くこと）のテスト
# ============================================================

class TestFallbackResponse:
    def test_fallback_response_for_known_substitute_question(self):
        """フォールバック時も、既知の代替食材質問には層1フィルタ済みの回答を返せること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        answer = build_fallback_response("玉ねぎ切らした、代わりある？", context)
        assert "玉ねぎ" in answer

    def test_fallback_response_respects_layer1_even_without_live_api(self):
        """フォールバック応答でも層1（アレルギー）フィルタが適用されること。"""
        context = MealPlanContext(
            meal_plan=_make_meal_plan(["にんにく 1片"]),
            review_profile=ReviewProfile(
                allergies=["生姜", "にんにくチューブ", "にんにくパウダー"]
            ),
        )
        answer = build_fallback_response("にんにくが無い、代わりは？", context)
        assert "生姜" not in answer
        assert "できませんでした" in answer or "変更" in answer

    def test_fallback_response_for_unrelated_question(self):
        """代替食材と無関係な質問には汎用の案内文を返すこと。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        answer = build_fallback_response("今日の天気は？", context)
        assert "接続できません" in answer or "画面" in answer


# ============================================================
# モデル名の設定
# ============================================================

class TestLiveModelName:
    def test_default_model_is_live_capable(self, monkeypatch):
        """デフォルトでは Live API 対応が明示されたモデルを使うこと
        （gemini-3.1-flash-lite ではなく、live-preview系モデル）。"""
        monkeypatch.delenv("GEMINI_LIVE_MODEL", raising=False)
        model = get_live_model_name()
        assert "live" in model.lower()

    def test_model_name_overridable_via_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_LIVE_MODEL", "gemini-custom-live-model")
        assert get_live_model_name() == "gemini-custom-live-model"
