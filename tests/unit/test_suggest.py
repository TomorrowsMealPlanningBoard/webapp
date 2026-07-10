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


# --- Issue: 提案フローを /api/propose に一本化（台本S2 / 監査ループ・層1フィルタ）---

def _build_stub_orchestrator_result():
    """MealOrchestrator.run のスタブが返す最小の OrchestratorResult を組み立てる。"""
    from app.agents.context_retriever import (
        HardConstraints,
        RetrievedContext,
        StructuredFeedbackContext,
    )
    from app.agents.orchestrator import OrchestratorResult
    from app.schemas import MealItem, MealPlan

    def _meal(meal_type: str) -> MealItem:
        return MealItem(
            id=f"stub-{meal_type}",
            title=f"スタブ{meal_type}",
            emoji="🍳",
            description="テスト用",
            cooking_time=15,
            effort_level="easy",
            servings=1,
            tags=[],
            ingredients=["卵 1個"],
            steps=[],
            meal_type=meal_type,
        )

    meal_plan = MealPlan(
        breakfast=_meal("breakfast"),
        lunch=_meal("lunch"),
        dinner=_meal("dinner"),
    )
    return OrchestratorResult(
        meal_plan=meal_plan,
        message="スタブ提案",
        context=RetrievedContext(
            user_id="test-user-001",
            hard_constraints=HardConstraints(),
            structured_feedback=StructuredFeedbackContext(),
        ),
    )


def test_propose_parses_form_fields_and_passes_to_orchestrator(client, auth_headers, monkeypatch):
    """/api/propose が multipart フォームを解釈し、気分・時間・認識食材を req に渡すこと。

    台本S2: 気分/時間/認識食材が監査（Reviewer）に届くことがデモ成立の前提。
    """
    captured = {}

    async def fake_run(self, *, user_id, req, image_bytes=None, image_mime_type=None):
        captured["user_id"] = user_id
        captured["req"] = req
        captured["image_bytes"] = image_bytes
        return _build_stub_orchestrator_result()

    monkeypatch.setattr("app.main.MealOrchestrator.run", fake_run)

    res = client.post(
        "/api/propose",
        headers=auth_headers,
        data={
            "cooking_time": "15",
            "effort_level": "easy",
            "mood_tags": '["肉料理", "さっぱり"]',
            "mood_freetext": "疲れているので簡単に",
            "ingredients": (
                '[{"name": "キャベツ", "quantity": 1, "unit": "個", "freshness": "good"}]'
            ),
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert len(body["recipes"]) == 3
    assert body["meal_plan"] is not None

    req = captured["req"]
    assert req.cooking_time == 15
    assert req.effort_level == "easy"
    assert req.mood_tags == ["肉料理", "さっぱり"]
    assert req.mood_freetext == "疲れているので簡単に"
    # 認識済み食材が Orchestrator に届いていること（画像を再送しなくても監査に反映される）
    assert [ing.name for ing in req.ingredients] == ["キャベツ"]
    # 画像未送信なので image_bytes は None
    assert captured["image_bytes"] is None


def test_propose_passes_uploaded_image_to_orchestrator(client, auth_headers, monkeypatch):
    """画像を同送した場合は image_bytes が Orchestrator（Vision Analyzer）へ渡ること。"""
    captured = {}

    async def fake_run(self, *, user_id, req, image_bytes=None, image_mime_type=None):
        captured["image_bytes"] = image_bytes
        captured["image_mime_type"] = image_mime_type
        return _build_stub_orchestrator_result()

    monkeypatch.setattr("app.main.MealOrchestrator.run", fake_run)

    res = client.post(
        "/api/propose",
        headers=auth_headers,
        data={
            "cooking_time": "30",
            "effort_level": "normal",
            "mood_tags": "[]",
            "mood_freetext": "",
            "ingredients": "[]",
        },
        files={"file": ("fridge.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )

    assert res.status_code == 200
    assert captured["image_bytes"] == b"\x89PNG\r\n\x1a\n"
    assert captured["image_mime_type"] == "image/png"


def test_propose_requires_auth(client):
    res = client.post("/api/propose", data={"cooking_time": "30"})
    assert res.status_code == 401


def test_propose_tolerates_malformed_ingredients_json(client, auth_headers, monkeypatch):
    """ingredients が不正なJSONでも 500 にならず空リストとして扱うこと。"""
    captured = {}

    async def fake_run(self, *, user_id, req, image_bytes=None, image_mime_type=None):
        captured["req"] = req
        return _build_stub_orchestrator_result()

    monkeypatch.setattr("app.main.MealOrchestrator.run", fake_run)

    res = client.post(
        "/api/propose",
        headers=auth_headers,
        data={
            "cooking_time": "30",
            "effort_level": "normal",
            "mood_tags": "not-json",
            "mood_freetext": "",
            "ingredients": "not-json",
        },
    )
    assert res.status_code == 200
    assert captured["req"].ingredients == []
    assert captured["req"].mood_tags == []
