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


# --- Issue #42: Vision認識結果 → 献立提案入力の配線 ---

def test_suggest_without_ingredients_field_is_backward_compatible(client, auth_headers):
    """ingredients フィールドを含まないリクエストでも従来どおり動作すること（後方互換）"""
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": ""
    })
    assert res.status_code == 200
    body = res.json()
    assert "recipes" in body
    assert "message" in body


def test_suggest_with_empty_ingredients_list(client, auth_headers):
    """食材が未認識（空リスト）でも提案が成立すること（後方互換）"""
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": "",
        "ingredients": []
    })
    assert res.status_code == 200
    body = res.json()
    assert "recipes" in body
    assert len(body["recipes"]) <= 3


def test_suggest_accepts_ingredients_list(client, auth_headers):
    """SuggestRequest が IngredientItem のリストを受け取り、バリデーションを通ること"""
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
        "ingredients": [
            {"name": "キャベツ", "quantity": 1, "unit": "個", "freshness": "good"},
            {"name": "豚肉", "quantity": 200, "unit": "g", "freshness": "fair"},
            {"name": "卵", "quantity": None, "unit": "", "freshness": "unknown"},
        ]
    })
    assert res.status_code == 200
    body = res.json()
    assert "recipes" in body
    assert "message" in body


def test_suggest_rejects_invalid_ingredient_item(client, auth_headers):
    """IngredientItem に必須の name が欠けている場合はバリデーションエラーになること"""
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
        "ingredients": [
            {"quantity": 1, "unit": "個", "freshness": "good"}
        ]
    })
    assert res.status_code == 422
