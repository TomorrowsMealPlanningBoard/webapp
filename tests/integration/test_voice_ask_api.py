"""
統合テスト: WebSocket /api/voice/session（Issue #39 / Gemini Live）

実際のマイク・スピーカー・ブラウザ操作を伴うE2Eはこの自動テストでは実施できないため
（PR本文に記載のうえローカル手動確認に委ねる）、その代替として
「WebSocket接続 → start メッセージ → Gemini Live セッション → イベント配信 → 終了」
という一連のAPIレベルのフローをモックで検証する。

外部依存（Gemini Live API・Context Retriever のDBアクセス経路にあるユーザープロファイル）
はテスト用SQLiteとモックで差し替える。
"""
from __future__ import annotations

from unittest.mock import patch

from app.agents.voice_session import VoiceSessionEvent, VoiceSessionUnavailableError
from app.auth import create_access_token


def _build_meal_plan_payload(ingredients: list[str]) -> dict:
    recipe = {
        "id": "recipe_1",
        "title": "玉ねぎと鶏肉の炒め物",
        "emoji": "🍳",
        "description": "テスト用レシピ",
        "cooking_time": 20,
        "effort_level": "normal",
        "servings": 2,
        "tags": ["和食"],
        "ingredients": ingredients,
        "steps": [{"step": 1, "description": "切る"}, {"step": 2, "description": "炒める"}],
        "required_tools": [],
    }
    item = {**recipe, "meal_type": "dinner"}
    return {"breakfast": item, "lunch": item, "dinner": item}


async def _fake_run_success(*args, **kwargs):
    yield VoiceSessionEvent(type="audio", audio_data=b"\x01\x02")
    yield VoiceSessionEvent(type="turn_complete")


async def _fake_run_unavailable(*args, **kwargs):
    raise VoiceSessionUnavailableError("live api down")
    yield  # pragma: no cover — 型を async generator にするためのダミー


class TestVoiceSessionWebSocketHappyPath:
    def test_voice_session_streams_audio_on_success(self, client, test_user):
        token = create_access_token(data={"sub": test_user.uid})
        with patch(
            "app.agents.voice_session.VoiceCookingSession.run",
            new=_fake_run_success,
        ):
            with client.websocket_connect(f"/api/voice/session?token={token}") as ws:
                ws.send_json({
                    "type": "start",
                    "meal_plan": _build_meal_plan_payload(["玉ねぎ 1個", "鶏むね肉 200g"]),
                })
                message = ws.receive_bytes()
                assert message == b"\x01\x02"
                message = ws.receive_json()
                assert message == {"type": "turn_complete"}

    def test_voice_session_requires_auth(self, client):
        with client.websocket_connect("/api/voice/session?token=invalid-token") as ws:
            message = ws.receive()
            assert message["type"] == "websocket.close"
            assert message["code"] == 1008


class TestVoiceSessionWebSocketFallback:
    """
    AC: 「音声機能が未対応環境でもコア献立提案が動作すること（機能のフォールバック）」
    Live API接続失敗時、例外を握ってフォールバック通知を送ることを検証する。
    """

    def test_voice_session_sends_fallback_when_live_api_unavailable(self, client, test_user):
        token = create_access_token(data={"sub": test_user.uid})
        with patch(
            "app.agents.voice_session.VoiceCookingSession.run",
            new=_fake_run_unavailable,
        ):
            with client.websocket_connect(f"/api/voice/session?token={token}") as ws:
                ws.send_json({
                    "type": "start",
                    "meal_plan": _build_meal_plan_payload(["玉ねぎ 1個"]),
                })
                message = ws.receive_json()
                assert message["type"] == "fallback"
                assert "アプリ画面" in message["message"]


class TestVoiceSessionWebSocketLayer1Constraints:
    """
    AC: 「応答時に層1のハード制約（アレルギー・NG食材・器具）が引き続き尊重されること」
    「代替提案が現在の献立コンテキストを踏まえた内容であること」

    層1制約の反映先である MealPlanContext / ReviewProfile の構築ロジック自体は
    app/main.py の voice_session_ws 内で Context Retriever Agent の結果から
    組み立てられる。ここでは、その構築済み ReviewProfile が
    VoiceCookingSession に正しく渡ることを検証する（Live API呼び出し自体はモック）。
    """

    def test_voice_session_builds_review_profile_from_user_allergies(
        self, client, db, test_user
    ):
        test_user.preferences = {
            "allergies": ["生姜"],
            "dislikes": [],
            "goal": "other",
            "kitchen_tools": [],
        }
        db.commit()
        token = create_access_token(data={"sub": test_user.uid})

        captured_context = {}

        class _CapturingSession:
            def __init__(self, context, recipe_id=None):
                captured_context["context"] = context

            async def run(self, audio_in):
                raise VoiceSessionUnavailableError("live api down")
                yield  # pragma: no cover

        with patch("app.agents.voice_session.VoiceCookingSession", _CapturingSession):
            with client.websocket_connect(f"/api/voice/session?token={token}") as ws:
                ws.send_json({
                    "type": "start",
                    "meal_plan": _build_meal_plan_payload(["にんにく 1片"]),
                })
                ws.receive_json()

        assert "生姜" in captured_context["context"].review_profile.allergies
