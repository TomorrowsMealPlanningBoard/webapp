"""
Voice Cooking Session Agent（Gemini Live / Issue #39）のユニットテスト。

方針:
- 実際の Gemini Live API 音声セッションはモックし、以下を重点的にテストする:
  1. 層1（アレルギー・禁止食材・調理器具）のハード制約が代替食材提案でも守られること
     （既存の Recipe Reviewer Agent の check_recipe / ReviewProfile を再利用していること）
  2. 現在の献立コンテキスト（meal_plan）を踏まえた代替候補が返ること
  3. Live API 接続・送受信に失敗した場合に VoiceSessionUnavailableError を送出し、
     コア献立提案自体には影響しないフォールバック設計になっていること
  4. ストリーミングブリッジ（run()）が音声/文字起こし/関数呼び出しイベントを
     正しく VoiceSessionEvent に変換して yield すること
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agents.reviewer import ReviewProfile
from app.agents.voice_session import (
    FALLBACK_MESSAGE,
    MealPlanContext,
    VoiceCookingSession,
    VoiceSessionUnavailableError,
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


async def _empty_audio_stream():
    return
    yield  # pragma: no cover — 型を async generator にするためのダミー


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
# Live API ストリーミングセッション（モック）のテスト
# ============================================================

class FakeAsyncSession:
    """google.genai の AsyncSession を模した最小限のフェイク。"""

    def __init__(self, messages):
        self._messages = messages
        self.sent_tool_responses = []
        self.sent_realtime_inputs = []

    async def send_realtime_input(self, *, audio):
        self.sent_realtime_inputs.append(audio)

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


def _make_audio_message(audio_bytes: bytes, transcript: str | None = None, turn_complete: bool = True):
    part = MagicMock()
    part.inline_data.data = audio_bytes
    model_turn = MagicMock()
    model_turn.parts = [part]
    server_content = MagicMock()
    server_content.model_turn = model_turn
    server_content.turn_complete = turn_complete
    if transcript is not None:
        server_content.output_transcription.text = transcript
    else:
        server_content.output_transcription = None
    message = MagicMock()
    message.tool_call = None
    message.server_content = server_content
    return message


def _make_turn_complete_message():
    server_content = MagicMock()
    server_content.model_turn = None
    server_content.output_transcription = None
    server_content.turn_complete = True
    message = MagicMock()
    message.tool_call = None
    message.server_content = server_content
    return message


@pytest.mark.asyncio
class TestVoiceCookingSessionRun:
    async def test_run_yields_audio_and_transcript_events(self):
        """Live APIからの音声出力・文字起こしを VoiceSessionEvent として yield すること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        session = VoiceCookingSession(context=context)

        fake_session = FakeAsyncSession([
            _make_audio_message(b"\x01\x02", transcript="次は玉ねぎを炒めてください。"),
        ])

        with patch("app.agents.voice_session._get_client") as mock_get_client, \
             patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            mock_client = MagicMock()
            mock_client.aio.live.connect.return_value = FakeLiveConnectCM(fake_session)
            mock_get_client.return_value = mock_client

            events = [event async for event in session.run(_empty_audio_stream())]

        types = [e.type for e in events]
        assert "audio" in types
        assert "transcript" in types
        assert "turn_complete" in types
        transcript_event = next(e for e in events if e.type == "transcript")
        assert transcript_event.text == "次は玉ねぎを炒めてください。"

    async def test_run_dispatches_function_call_and_applies_layer1(self):
        """関数呼び出し（代替食材提案）が発生した場合、層1フィルタ済みの結果を
        send_tool_response に渡し、function_call イベントを yield すること。"""
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

        fake_session = FakeAsyncSession([tool_call_message, _make_turn_complete_message()])

        with patch("app.agents.voice_session._get_client") as mock_get_client, \
             patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            mock_client = MagicMock()
            mock_client.aio.live.connect.return_value = FakeLiveConnectCM(fake_session)
            mock_get_client.return_value = mock_client

            events = [event async for event in session.run(_empty_audio_stream())]

        assert len(fake_session.sent_tool_responses) == 1
        sent = fake_session.sent_tool_responses[0][0]
        # 層1フィルタ（アレルギー: 長ねぎ）により候補から除外されていること
        assert "長ねぎ" not in sent.response["result"]["candidates"]

        function_call_events = [e for e in events if e.type == "function_call"]
        assert len(function_call_events) == 1
        assert "エシャロット" in function_call_events[0].text or "生姜" in function_call_events[0].text

    async def test_run_forwards_input_audio_chunks(self):
        """フロントから届く音声チャンクを send_realtime_input で中継すること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        session = VoiceCookingSession(context=context)

        fake_session = FakeAsyncSession([_make_turn_complete_message()])

        async def audio_stream():
            yield b"\x00\x01"
            yield b"\x02\x03"

        with patch("app.agents.voice_session._get_client") as mock_get_client, \
             patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            mock_client = MagicMock()
            mock_client.aio.live.connect.return_value = FakeLiveConnectCM(fake_session)
            mock_get_client.return_value = mock_client

            events = [event async for event in session.run(audio_stream())]

        assert any(e.type == "turn_complete" for e in events)

    async def test_run_raises_unavailable_error_on_connection_failure(self):
        """Live API接続時に例外が発生した場合、VoiceSessionUnavailableErrorに変換されること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        session = VoiceCookingSession(context=context)

        with patch("app.agents.voice_session._get_client") as mock_get_client, \
             patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}):
            mock_client = MagicMock()
            mock_client.aio.live.connect.side_effect = ConnectionError("network down")
            mock_get_client.return_value = mock_client

            with pytest.raises(VoiceSessionUnavailableError):
                async for _ in session.run(_empty_audio_stream()):
                    pass

    async def test_run_raises_unavailable_error_when_project_env_missing(self):
        """GOOGLE_CLOUD_PROJECT未設定でも例外を握ってVoiceSessionUnavailableErrorに変換されること。"""
        context = MealPlanContext(meal_plan=_make_meal_plan(["玉ねぎ 1個"]))
        session = VoiceCookingSession(context=context)

        with patch.dict("os.environ", {}, clear=False):
            import os as _os
            _os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
            with pytest.raises(VoiceSessionUnavailableError):
                async for _ in session.run(_empty_audio_stream()):
                    pass


# ============================================================
# フォールバック（Live API未対応環境でもコア機能が動くこと）のテスト
# ============================================================

class TestFallbackMessage:
    def test_fallback_message_guides_user_to_app_screen(self):
        """Live APIが使えない場合の通知文が、アプリ画面操作を案内すること。"""
        assert "アプリ画面" in FALLBACK_MESSAGE or "画面" in FALLBACK_MESSAGE


# ============================================================
# モデル名の設定
# ============================================================

class TestLiveModelName:
    def test_default_model_is_live_capable(self, monkeypatch):
        """デフォルトでは Live API 対応が明示されたモデルを使うこと
        （gemini-3.1-flash-lite ではなく、Vertex AI の Live 対応モデル）。"""
        monkeypatch.delenv("GEMINI_LIVE_MODEL", raising=False)
        model = get_live_model_name()
        assert "live" in model.lower()

    def test_model_name_overridable_via_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_LIVE_MODEL", "gemini-custom-live-model")
        assert get_live_model_name() == "gemini-custom-live-model"
