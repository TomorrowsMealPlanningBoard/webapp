"""
Issue #23: 提案された食事に対する評価・フィードバック機能のユニットテスト
SPEC.md §5.3「フィードバックループのUX/データフロー」に基づく。
"""
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
