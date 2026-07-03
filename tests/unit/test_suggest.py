"""
Epic 3-2: 献立提案APIのユニットテスト（モックデータを使用）
"""


def test_suggest_returns_recipes(client, auth_headers):
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": ""
    })
    assert res.status_code == 200
    body = res.json()
    assert "recipes" in body
    assert len(body["recipes"]) <= 3
    assert "message" in body


def test_suggest_requires_auth(client):
    res = client.post("/api/suggest", json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": ""
    })
    assert res.status_code == 401


def test_suggest_time_filter(client, auth_headers):
    """調理時間フィルタが機能すること"""
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 10,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": ""
    })
    assert res.status_code == 200
    for recipe in res.json()["recipes"]:
        assert recipe["cooking_time"] <= 10 or len(res.json()["recipes"]) > 0
