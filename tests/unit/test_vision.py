"""
Epic 2-1: POST /api/vision ユニットテスト
Gemini API は monkeypatch でモックし、ネットワーク通信なしで検証する。
"""
import io
import json
from unittest.mock import MagicMock, patch

import pytest

from app.agents.vision_analyzer import VisionAnalysisResult, Ingredient


# ------------------------------------------------------------------ helpers --

def _make_jpeg(size: int = 16) -> bytes:
    """最小限の合法的な JPEG バイト列を返す"""
    return b"\xff\xd8\xff\xe0" + b"\x00" * size


def _mock_analyze(ingredients: list[dict]):
    """vision_analyzer.analyze_image をモックするパッチを返す"""
    result = VisionAnalysisResult(
        ingredients=[Ingredient(**i) for i in ingredients]
    )
    return patch("app.main.vision_analyzer.analyze_image", return_value=result)


# ------------------------------------------------------------------ tests --

def test_vision_returns_ingredient_list(client, auth_headers):
    """正常系: 画像をPOSTすると食材リストが返る"""
    ingredients = [
        {"name": "卵", "quantity": 3, "unit": "個", "freshness": "good"},
        {"name": "牛乳", "quantity": 200.0, "unit": "ml", "freshness": "good"},
    ]
    with _mock_analyze(ingredients):
        res = client.post(
            "/api/vision",
            headers=auth_headers,
            files={"file": ("fridge.jpg", _make_jpeg(), "image/jpeg")},
        )

    assert res.status_code == 200
    body = res.json()
    assert "ingredients" in body
    assert len(body["ingredients"]) == 2
    assert body["ingredients"][0]["name"] == "卵"
    assert body["ingredients"][0]["quantity"] == 3
    assert body["ingredients"][0]["unit"] == "個"
    assert body["ingredients"][0]["freshness"] == "good"


def test_vision_requires_auth(client):
    """未認証リクエストは 401 を返す"""
    res = client.post(
        "/api/vision",
        files={"file": ("fridge.jpg", _make_jpeg(), "image/jpeg")},
    )
    assert res.status_code == 401


def test_vision_rejects_unsupported_mime(client, auth_headers):
    """非画像ファイル（text/plain）は 400 を返す"""
    res = client.post(
        "/api/vision",
        headers=auth_headers,
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 400


def test_vision_empty_image_returns_400(client, auth_headers):
    """空バイト列を送ると 400 を返す"""
    with patch(
        "app.main.vision_analyzer.analyze_image",
        side_effect=ValueError("画像データが空です"),
    ):
        res = client.post(
            "/api/vision",
            headers=auth_headers,
            files={"file": ("empty.jpg", b"", "image/jpeg")},
        )
    assert res.status_code == 400


def test_vision_unrecognizable_image_returns_400(client, auth_headers):
    """AI が認識できない画像は 400 を返す"""
    with patch(
        "app.main.vision_analyzer.analyze_image",
        side_effect=ValueError("画像から食材を認識できませんでした"),
    ):
        res = client.post(
            "/api/vision",
            headers=auth_headers,
            files={"file": ("blank.jpg", _make_jpeg(), "image/jpeg")},
        )
    assert res.status_code == 400
    assert "認識" in res.json()["detail"]


def test_vision_includes_allergy_ingredients(client, auth_headers):
    """アレルギー食材（例: 卵）も除外せず返す（除外は Reviewer Agent の責務）"""
    ingredients = [
        {"name": "卵", "quantity": 6, "unit": "個", "freshness": "good"},
        {"name": "小麦粉", "quantity": None, "unit": "g", "freshness": "unknown"},
    ]
    with _mock_analyze(ingredients):
        res = client.post(
            "/api/vision",
            headers=auth_headers,
            files={"file": ("fridge.jpg", _make_jpeg(), "image/jpeg")},
        )

    assert res.status_code == 200
    names = [i["name"] for i in res.json()["ingredients"]]
    assert "卵" in names
    assert "小麦粉" in names


def test_vision_png_is_accepted(client, auth_headers):
    """PNG 画像も受け付ける"""
    ingredients = [{"name": "トマト", "quantity": 2, "unit": "個", "freshness": "good"}]
    with _mock_analyze(ingredients):
        res = client.post(
            "/api/vision",
            headers=auth_headers,
            files={"file": ("fridge.png", _make_jpeg(), "image/png")},
        )
    assert res.status_code == 200
