"""
統合テスト: POST /api/voice/ask（Issue #39 / Gemini Live）

実際の音声デバイス・ブラウザ操作を伴うE2Eはこの自動テストでは実施できないため
（PR本文に記載のうえローカル手動確認に委ねる）、その代替として
「音声セッション確立 → テキスト化された質問 → 応答 → 層1チェック通過」という
一連のAPIレベルのフローをモックで検証する。

外部依存（Gemini Live API・Context Retriever のDBアクセス経路にあるユーザープロファイル）
はテスト用SQLiteとモックで差し替える。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.agents.voice_session import VoiceSessionUnavailableError


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


class TestVoiceAskApiHappyPath:
    def test_voice_ask_returns_live_api_response(self, client, auth_headers):
        """Live API呼び出しが成功した場合、その応答テキストを返すこと。"""
        with patch(
            "app.main.voice_session_module.ask_cooking_assistant",
            new=AsyncMock(return_value="次は炒めてください。"),
        ):
            resp = client.post(
                "/api/voice/ask",
                json={
                    "question_text": "次どうする？",
                    "meal_plan": _build_meal_plan_payload(["玉ねぎ 1個", "鶏むね肉 200g"]),
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer_text"] == "次は炒めてください。"
        assert data["used_fallback"] is False

    def test_voice_ask_requires_auth(self, client):
        resp = client.post(
            "/api/voice/ask",
            json={"question_text": "次どうする？"},
        )
        assert resp.status_code == 401


class TestVoiceAskApiFallback:
    """
    AC: 「音声機能が未対応環境でもコア献立提案が動作すること（機能のフォールバック）」
    Live API接続失敗時、例外を握って通常のフォールバック応答に切り替わることを検証する。
    """

    def test_voice_ask_falls_back_when_live_api_unavailable(self, client, auth_headers):
        with patch(
            "app.main.voice_session_module.ask_cooking_assistant",
            new=AsyncMock(side_effect=VoiceSessionUnavailableError("live api down")),
        ):
            resp = client.post(
                "/api/voice/ask",
                json={
                    "question_text": "玉ねぎ切らした、代わりある？",
                    "meal_plan": _build_meal_plan_payload(["玉ねぎ 1個"]),
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["used_fallback"] is True
        # フォールバックでも代替食材の案内文が返ること（層1フィルタ通過済み候補ベース）
        assert "玉ねぎ" in data["answer_text"]

    def test_voice_ask_falls_back_on_unexpected_error(self, client, auth_headers):
        """予期しない例外でもAPIが500にならず、フォールバック応答を返すこと。"""
        with patch(
            "app.main.voice_session_module.ask_cooking_assistant",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            resp = client.post(
                "/api/voice/ask",
                json={"question_text": "次どうする？"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["used_fallback"] is True


class TestVoiceAskApiLayer1Constraints:
    """
    AC: 「応答時に層1のハード制約（アレルギー・NG食材・器具）が引き続き尊重されること」
    「代替提案が現在の献立コンテキストを踏まえた内容であること」
    """

    def test_voice_ask_uses_user_allergy_profile_for_fallback_substitute(
        self, client, auth_headers, db, test_user
    ):
        """
        ユーザープロファイルのアレルギー設定が Context Retriever 経由で
        ReviewProfile に反映され、フォールバック応答の代替候補からも除外されること。
        """
        test_user.preferences = {
            "allergies": ["生姜"],
            "dislikes": [],
            "goal": "other",
            "kitchen_tools": [],
        }
        db.commit()

        with patch(
            "app.main.voice_session_module.ask_cooking_assistant",
            new=AsyncMock(side_effect=VoiceSessionUnavailableError("live api down")),
        ):
            resp = client.post(
                "/api/voice/ask",
                json={
                    "question_text": "にんにくが無い、代わりは？",
                    "meal_plan": _build_meal_plan_payload(["にんにく 1片"]),
                },
                headers=auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["used_fallback"] is True
        # アレルギー物質「生姜」を含む代替候補が案内文に出ていないこと
        assert "生姜" not in data["answer_text"]
