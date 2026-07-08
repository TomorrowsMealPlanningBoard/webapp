"""
Issue #40: 能動的な自律提案ロジックのユニットテスト

テスト対象:
- get_expiring_ingredients_suggestion: 賞味期限優先提案
- get_nutrition_adjustment_suggestion: 栄養調整提案
- GET /api/proactive エンドポイント
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.proactive import (
    get_calendar_meal_prep_suggestion,
    get_expiring_ingredients_suggestion,
    get_nutrition_adjustment_suggestion,
    get_proactive_suggestions,
)
from app.firestore_store import UserDoc


# ============================================================
# テスト用ヘルパー
# ============================================================

def make_user(uid: str = "test-user", preferences: dict | None = None) -> UserDoc:
    """テスト用の UserDoc インスタンスを作成する（Firestoreに保存しない）。"""
    return UserDoc({
        "uid": uid,
        "email": f"{uid}@example.com",
        "display_name": "テストユーザー",
        "preferences": preferences or {},
    })


def future_date(days: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.isoformat()


def past_date(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


# ============================================================
# get_expiring_ingredients_suggestion のテスト
# ============================================================

class TestGetExpiringIngredientsSuggestion:
    def test_returns_none_when_no_ingredients(self):
        user = make_user(preferences={})
        result = get_expiring_ingredients_suggestion(user)
        assert result is None

    def test_returns_none_when_no_expiry_date(self):
        user = make_user(preferences={
            "ingredients": [{"name": "キャベツ", "quantity": 1, "unit": "個"}]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is None

    def test_returns_none_when_all_future_ingredients(self):
        user = make_user(preferences={
            "ingredients": [
                {"name": "にんじん", "expiry_date": future_date(10)},
                {"name": "玉ねぎ", "expiry_date": future_date(14)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is None

    def test_returns_suggestion_when_expiring_ingredient_exists(self):
        user = make_user(preferences={
            "ingredients": [
                {"name": "豆腐", "expiry_date": future_date(1), "unit": "丁"},
                {"name": "にんじん", "expiry_date": future_date(10)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None
        assert result.trigger_type == "expiring"
        assert "豆腐" in result.reason
        assert result.urgency in ("high", "medium")

    def test_includes_expiring_ingredients_in_suggest_request(self):
        user = make_user(preferences={
            "ingredients": [
                {"name": "ほうれん草", "expiry_date": future_date(2), "unit": "束"},
                {"name": "鶏肉", "expiry_date": future_date(1), "unit": "g", "quantity": 300},
                {"name": "米", "expiry_date": future_date(30)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None
        ingredient_names = {i.name for i in result.suggest_request.ingredients}
        assert "ほうれん草" in ingredient_names
        assert "鶏肉" in ingredient_names
        assert "米" not in ingredient_names

    def test_expiring_on_boundary_day_is_included(self):
        user = make_user(preferences={
            "ingredients": [{"name": "卵", "expiry_date": future_date(3)}]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None

    def test_already_expired_ingredient_is_included(self):
        user = make_user(preferences={
            "ingredients": [{"name": "牛乳", "expiry_date": past_date(1)}]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None
        assert "牛乳" in result.reason

    def test_urgency_is_high_when_three_or_more_expiring(self):
        user = make_user(preferences={
            "ingredients": [
                {"name": "豆腐", "expiry_date": future_date(1)},
                {"name": "納豆", "expiry_date": future_date(2)},
                {"name": "ほうれん草", "expiry_date": future_date(3)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None
        assert result.urgency == "high"

    def test_urgency_is_medium_when_fewer_than_three_expiring(self):
        user = make_user(preferences={
            "ingredients": [
                {"name": "豆腐", "expiry_date": future_date(1)},
                {"name": "納豆", "expiry_date": future_date(2)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None
        assert result.urgency == "medium"

    def test_invalid_expiry_date_is_skipped(self):
        user = make_user(preferences={
            "ingredients": [
                {"name": "食材A", "expiry_date": "invalid-date"},
                {"name": "食材B", "expiry_date": future_date(10)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is None


# ============================================================
# get_nutrition_adjustment_suggestion のテスト
# ============================================================

class TestGetNutritionAdjustmentSuggestion:
    def test_returns_none_when_no_feedback(self, mock_firestore):
        """フィードバックがない場合は None を返すこと"""
        mock_firestore.add_user(uid="user-nutrition-0", email="user-nutrition-0@example.com")
        user = make_user(uid="user-nutrition-0")
        result = get_nutrition_adjustment_suggestion(user)
        assert result is None

    def test_returns_none_when_only_one_unhealthy_tag(self, mock_firestore):
        """不健康タグが1回しか出現しない場合は None を返すこと"""
        mock_firestore.add_user(uid="user-nutrition-1", email="user-nutrition-1@example.com")
        mock_firestore.add_feedback("user-nutrition-1", id=str(uuid.uuid4()), recipe_id="r1",
                                      feedback_type="cooked", tags=["#揚げ物"], rating=3)
        user = make_user(uid="user-nutrition-1")
        result = get_nutrition_adjustment_suggestion(user)
        assert result is None

    def test_returns_suggestion_when_fried_food_tag_appears_twice(self, mock_firestore):
        """#揚げ物 タグが2回以上出現した場合に野菜系の提案が返ること"""
        mock_firestore.add_user(uid="user-nutrition-2", email="user-nutrition-2@example.com")
        for i in range(3):
            mock_firestore.add_feedback("user-nutrition-2", id=str(uuid.uuid4()), recipe_id=f"r{i}",
                                          feedback_type="cooked", tags=["#揚げ物"], rating=4)
        user = make_user(uid="user-nutrition-2")
        result = get_nutrition_adjustment_suggestion(user)
        assert result is not None
        assert result.trigger_type == "nutrition"
        assert "野菜" in result.suggest_request.mood_freetext or "あっさり" in result.suggest_request.mood_freetext

    def test_reason_contains_trend_info(self, mock_firestore):
        mock_firestore.add_user(uid="user-nutrition-3", email="user-nutrition-3@example.com")
        for i in range(2):
            mock_firestore.add_feedback("user-nutrition-3", id=str(uuid.uuid4()), recipe_id=f"r{i}",
                                          feedback_type="cooked", tags=["#こってり"], rating=3)
        user = make_user(uid="user-nutrition-3")
        result = get_nutrition_adjustment_suggestion(user)
        assert result is not None
        assert "こってり" in result.reason

    def test_urgency_is_medium(self, mock_firestore):
        mock_firestore.add_user(uid="user-nutrition-4", email="user-nutrition-4@example.com")
        for i in range(5):
            mock_firestore.add_feedback("user-nutrition-4", id=str(uuid.uuid4()), recipe_id=f"r{i}",
                                          feedback_type="cooked", tags=["#揚げ物"], rating=5)
        user = make_user(uid="user-nutrition-4")
        result = get_nutrition_adjustment_suggestion(user)
        assert result is not None
        assert result.urgency == "medium"

    def test_returns_none_for_old_feedback(self, mock_firestore):
        """7日より古いフィードバックタグは分析対象外であること"""
        mock_firestore.add_user(uid="user-nutrition-5", email="user-nutrition-5@example.com")
        old_cutoff = datetime.now(timezone.utc) - timedelta(days=8)
        for i in range(3):
            mock_firestore.add_feedback("user-nutrition-5", id=str(uuid.uuid4()), recipe_id=f"r{i}",
                                          feedback_type="cooked", tags=["#揚げ物"], rating=3,
                                          created_at=old_cutoff)
        user = make_user(uid="user-nutrition-5")
        result = get_nutrition_adjustment_suggestion(user)
        assert result is None


# ============================================================
# get_calendar_meal_prep_suggestion のテスト
# ============================================================

class TestGetCalendarMealPrepSuggestion:
    def test_returns_none_always(self):
        user = make_user()
        result = get_calendar_meal_prep_suggestion(user)
        assert result is None


# ============================================================
# /api/proactive エンドポイントのテスト
# ============================================================

class TestProactiveEndpoint:
    def test_requires_authentication(self, client):
        res = client.get("/api/proactive")
        assert res.status_code == 401

    def test_returns_empty_list_when_no_triggers(self, client, auth_headers):
        res = client.get("/api/proactive", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert "suggestions" in body
        assert isinstance(body["suggestions"], list)
        assert body["suggestions"] == []

    def test_returns_expiring_suggestion_when_ingredients_expiring(
        self, client, auth_headers, test_user, mock_firestore
    ):
        mock_firestore.users[test_user.uid]["preferences"] = {
            "allergies": [], "dislikes": [], "goal": "other", "kitchen_tools": [],
            "ingredients": [
                {"name": "ほうれん草", "expiry_date": future_date(1), "unit": "束", "quantity": 1}
            ],
        }

        res = client.get("/api/proactive", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["suggestions"]) >= 1
        trigger_types = [s["trigger_type"] for s in body["suggestions"]]
        assert "expiring" in trigger_types

    def test_suggestion_has_required_fields(self, client, auth_headers, test_user, mock_firestore):
        mock_firestore.users[test_user.uid]["preferences"] = {
            "allergies": [], "dislikes": [], "goal": "other", "kitchen_tools": [],
            "ingredients": [{"name": "卵", "expiry_date": future_date(2), "unit": "個"}],
        }

        res = client.get("/api/proactive", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["suggestions"]) >= 1

        suggestion = body["suggestions"][0]
        assert "trigger_type" in suggestion
        assert "suggest_request" in suggestion
        assert "reason" in suggestion
        assert "urgency" in suggestion
        assert suggestion["trigger_type"] in ("expiring", "nutrition", "calendar")
        assert suggestion["urgency"] in ("high", "medium", "low")

    def test_returns_nutrition_suggestion_when_fried_food_feedback_exists(
        self, client, auth_headers, test_user, mock_firestore
    ):
        for i in range(3):
            mock_firestore.add_feedback(
                user_id=test_user.uid,
                id=str(uuid.uuid4()),
                recipe_id=f"recipe-{i}",
                feedback_type="cooked",
                tags=["#揚げ物"],
                rating=4,
            )

        res = client.get("/api/proactive", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        trigger_types = [s["trigger_type"] for s in body["suggestions"]]
        assert "nutrition" in trigger_types
