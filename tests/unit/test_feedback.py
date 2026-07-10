"""
Issue #23: 提案された食事に対する評価・フィードバック機能のユニットテスト
SPEC.md §5.3「フィードバックループのUX/データフロー」に基づく。
"""
import os
from unittest.mock import patch

from app.mock_recipes import MOCK_RECIPES


def test_feedback_reject_extracts_feature_tags(client, auth_headers):
    """不採用FB: レシピの特徴タグが自動抽出されて保存されること"""
    recipe = MOCK_RECIPES[0]
    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": recipe["id"],
        "recipe_title": recipe["title"],
        "feedback_type": "reject",
        "tags": [],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["recipe_id"] == recipe["id"]
    assert body["feedback_type"] == "reject"
    # レシピの tags が "#" 付きの特徴タグとして抽出されていること
    assert len(body["tags"]) > 0
    for tag in body["tags"]:
        assert tag.startswith("#")
    assert any(recipe["tags"][0] in tag for tag in body["tags"])


def test_feedback_reject_unknown_recipe_falls_back_to_request_tags(client, auth_headers):
    """不採用FB: モックレシピに存在しないIDの場合はリクエストのtagsをフォールバックに使う"""
    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": "unknown_recipe_999",
        "feedback_type": "reject",
        "tags": ["揚げ物", "豚肉"],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["tags"] == ["#揚げ物", "#豚肉"]


def test_feedback_reject_real_recipe_uses_llm_feature_tags(client, auth_headers):
    """
    不採用FB（SPEC §5.3 / 台本S3）: MOCK_RECIPES に無い実レシピ（LLM生成）で
    材料・手順が渡された場合、料理名ではなく Feature Tag Extractor（LLM）が抽出した
    特徴タグが '#' 付きで保存されること。
    """
    # PYTEST_CURRENT_TEST ガードを一時的に外し、実抽出パスを通す（Geminiはモックする）。
    env_without_pytest = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    with patch.dict(os.environ, env_without_pytest, clear=True), patch(
        "app.main.feature_tag_extractor_module.extract_feature_tags",
        return_value=["揚げ物", "豚肉", "こってり"],
    ) as mock_extract:
        res = client.post("/api/feedback", headers=auth_headers, json={
            "recipe_id": "llm_recipe_abc",
            "recipe_title": "とんかつ",
            "feedback_type": "reject",
            "tags": ["洋食"],
            "ingredients": ["豚ロース 200g", "パン粉 適量", "揚げ油 適量"],
            "steps": ["豚肉に衣をつける", "170度の油で揚げる"],
        })
    assert res.status_code == 200
    body = res.json()
    assert body["tags"] == ["#揚げ物", "#豚肉", "#こってり"]
    # 料理名ではなくレシピ本文がLLMに渡っていること
    assert mock_extract.call_count == 1
    kwargs = mock_extract.call_args.kwargs
    assert kwargs["ingredients"] == ["豚ロース 200g", "パン粉 適量", "揚げ油 適量"]
    assert kwargs["steps"] == ["豚肉に衣をつける", "170度の油で揚げる"]


def test_feedback_reject_llm_failure_falls_back_to_request_tags(client, auth_headers):
    """不採用FB: LLM抽出が例外を投げた場合は fallback（リクエストのtags）に確実に落ちること。"""
    env_without_pytest = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    with patch.dict(os.environ, env_without_pytest, clear=True), patch(
        "app.main.feature_tag_extractor_module.extract_feature_tags",
        side_effect=RuntimeError("Gemini timeout"),
    ):
        res = client.post("/api/feedback", headers=auth_headers, json={
            "recipe_id": "llm_recipe_xyz",
            "recipe_title": "からあげ",
            "feedback_type": "reject",
            "tags": ["揚げ物", "鶏肉"],
            "ingredients": ["鶏もも肉 300g"],
            "steps": ["下味をつけて揚げる"],
        })
    assert res.status_code == 200
    assert res.json()["tags"] == ["#揚げ物", "#鶏肉"]


def test_feedback_cooked_requires_rating(client, auth_headers):
    """調理後FB: rating（1〜5）が必須で、欠けている場合は400を返す"""
    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": "recipe_001",
        "feedback_type": "cooked",
        "tags": [],
    })
    assert res.status_code == 400


def test_feedback_cooked_with_rating_and_tags(client, auth_headers):
    """調理後FB: 星評価＋スマートチップタグ＋自由記述が保存されること"""
    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": "recipe_002",
        "recipe_title": "豚こまと野菜のみそ炒め",
        "feedback_type": "cooked",
        "tags": ["味付けが最高", "手軽だった"],
        "rating": 5,
        "comment": "とても美味しかったです",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["rating"] == 5
    assert body["tags"] == ["味付けが最高", "手軽だった"]
    assert body["comment"] == "とても美味しかったです"


def test_feedback_cooked_comment_is_optional(client, auth_headers):
    """調理後FB: 自由記述欄はオプションで、省略しても保存できること"""
    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": "recipe_003",
        "feedback_type": "cooked",
        "tags": ["量が多かった"],
        "rating": 2,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["comment"] is None
    assert body["rating"] == 2


def test_feedback_invalid_rating_out_of_range(client, auth_headers):
    """調理後FB: rating が1〜5の範囲外の場合は422を返す"""
    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": "recipe_001",
        "feedback_type": "cooked",
        "tags": [],
        "rating": 6,
    })
    assert res.status_code == 422


def test_feedback_invalid_feedback_type(client, auth_headers):
    """不正なfeedback_typeは400を返す"""
    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": "recipe_001",
        "feedback_type": "invalid_type",
        "tags": [],
    })
    assert res.status_code == 400


def test_feedback_requires_auth(client):
    """未認証リクエストは401を返す"""
    res = client.post("/api/feedback", json={
        "recipe_id": "recipe_001",
        "feedback_type": "reject",
        "tags": [],
    })
    assert res.status_code == 401
