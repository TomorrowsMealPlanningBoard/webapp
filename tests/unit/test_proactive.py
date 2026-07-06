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
from app.models import Feedback, User
from app.auth import get_password_hash


# ============================================================
# テスト用ヘルパー
# ============================================================

def make_user(uid: str = "test-user", preferences: dict | None = None) -> User:
    """テスト用の User インスタンスを作成する（DBに保存しない）。"""
    return User(
        uid=uid,
        email=f"{uid}@example.com",
        hashed_password=get_password_hash("pass"),
        display_name="テストユーザー",
        preferences=preferences or {},
    )


def future_date(days: int) -> str:
    """現在から `days` 日後の日付を ISO 8601 文字列で返す。"""
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.isoformat()


def past_date(days: int) -> str:
    """現在から `days` 日前の日付を ISO 8601 文字列で返す。"""
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


# ============================================================
# get_expiring_ingredients_suggestion のテスト
# ============================================================

class TestGetExpiringIngredientsSuggestion:
    """賞味期限優先提案のテスト。"""

    def test_returns_none_when_no_ingredients(self):
        """食材リストが空の場合は None を返すこと。"""
        user = make_user(preferences={})
        result = get_expiring_ingredients_suggestion(user)
        assert result is None

    def test_returns_none_when_no_expiry_date(self):
        """expiry_date がない食材は無視されること。"""
        user = make_user(preferences={
            "ingredients": [
                {"name": "キャベツ", "quantity": 1, "unit": "個"},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is None

    def test_returns_none_when_all_future_ingredients(self):
        """期限が3日より先の食材のみの場合は None を返すこと。"""
        user = make_user(preferences={
            "ingredients": [
                {"name": "にんじん", "expiry_date": future_date(10)},
                {"name": "玉ねぎ", "expiry_date": future_date(14)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is None

    def test_returns_suggestion_when_expiring_ingredient_exists(self):
        """期限3日以内の食材がある場合に提案を返すこと。"""
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
        """提案リクエストの ingredients に期限切れ間近の食材が含まれること。"""
        user = make_user(preferences={
            "ingredients": [
                {"name": "ほうれん草", "expiry_date": future_date(2), "unit": "束"},
                {"name": "鶏肉", "expiry_date": future_date(1), "unit": "g", "quantity": 300},
                {"name": "米", "expiry_date": future_date(30)},  # 対象外
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None
        ingredient_names = {i.name for i in result.suggest_request.ingredients}
        assert "ほうれん草" in ingredient_names
        assert "鶏肉" in ingredient_names
        assert "米" not in ingredient_names

    def test_expiring_on_boundary_day_is_included(self):
        """ちょうど3日後（境界値）の食材も対象に含まれること。"""
        user = make_user(preferences={
            "ingredients": [
                {"name": "卵", "expiry_date": future_date(3)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None

    def test_already_expired_ingredient_is_included(self):
        """既に期限切れの食材も提案に含まれること（使い切り促進）。"""
        user = make_user(preferences={
            "ingredients": [
                {"name": "牛乳", "expiry_date": past_date(1)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        assert result is not None
        assert "牛乳" in result.reason

    def test_urgency_is_high_when_three_or_more_expiring(self):
        """期限切れ間近の食材が3つ以上ある場合は urgency が 'high' になること。"""
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
        """期限切れ間近の食材が2つ以下の場合は urgency が 'medium' になること。"""
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
        """不正な expiry_date は無視されること（エラーにならないこと）。"""
        user = make_user(preferences={
            "ingredients": [
                {"name": "食材A", "expiry_date": "invalid-date"},
                {"name": "食材B", "expiry_date": future_date(10)},
            ]
        })
        result = get_expiring_ingredients_suggestion(user)
        # invalid date は無視され、future_date(10) も対象外なので None
        assert result is None


# ============================================================
# get_nutrition_adjustment_suggestion のテスト
# ============================================================

class TestGetNutritionAdjustmentSuggestion:
    """栄養調整提案のテスト。"""

    def test_returns_none_when_no_feedback(self, db):
        """フィードバックがない場合は None を返すこと。"""
        user = make_user()
        db.add(user)
        db.commit()

        result = get_nutrition_adjustment_suggestion(user, db)
        assert result is None

    def test_returns_none_when_only_one_unhealthy_tag(self, db):
        """不健康タグが1回しか出現しない場合は None を返すこと（閾値: 2回以上）。"""
        user = make_user(uid="user-nutrition-1")
        db.add(user)
        db.commit()

        fb = Feedback(
            id=str(uuid.uuid4()),
            user_id=user.uid,
            recipe_id="recipe-1",
            feedback_type="cooked",
            tags=["#揚げ物"],
            rating=3,
        )
        db.add(fb)
        db.commit()

        result = get_nutrition_adjustment_suggestion(user, db)
        assert result is None

    def test_returns_suggestion_when_fried_food_tag_appears_twice(self, db):
        """#揚げ物 タグが2回以上出現した場合に野菜系の提案が返ること。"""
        user = make_user(uid="user-nutrition-2")
        db.add(user)
        db.commit()

        for i in range(3):
            fb = Feedback(
                id=str(uuid.uuid4()),
                user_id=user.uid,
                recipe_id=f"recipe-{i}",
                feedback_type="cooked",
                tags=["#揚げ物"],
                rating=4,
            )
            db.add(fb)
        db.commit()

        result = get_nutrition_adjustment_suggestion(user, db)
        assert result is not None
        assert result.trigger_type == "nutrition"
        # 野菜多め・あっさりの方向が提案内容に反映されること
        assert "野菜" in result.suggest_request.mood_freetext or "あっさり" in result.suggest_request.mood_freetext

    def test_reason_contains_trend_info(self, db):
        """reason に傾向の説明が含まれること。"""
        user = make_user(uid="user-nutrition-3")
        db.add(user)
        db.commit()

        for i in range(2):
            fb = Feedback(
                id=str(uuid.uuid4()),
                user_id=user.uid,
                recipe_id=f"recipe-{i}",
                feedback_type="cooked",
                tags=["#こってり"],
                rating=3,
            )
            db.add(fb)
        db.commit()

        result = get_nutrition_adjustment_suggestion(user, db)
        assert result is not None
        assert "こってり" in result.reason

    def test_urgency_is_medium(self, db):
        """栄養調整提案の urgency は常に 'medium' であること。"""
        user = make_user(uid="user-nutrition-4")
        db.add(user)
        db.commit()

        for i in range(5):
            fb = Feedback(
                id=str(uuid.uuid4()),
                user_id=user.uid,
                recipe_id=f"recipe-{i}",
                feedback_type="cooked",
                tags=["#揚げ物"],
                rating=5,
            )
            db.add(fb)
        db.commit()

        result = get_nutrition_adjustment_suggestion(user, db)
        assert result is not None
        assert result.urgency == "medium"

    def test_returns_none_for_old_feedback(self, db):
        """7日より古いフィードバックタグは分析対象外であること。"""
        user = make_user(uid="user-nutrition-5")
        db.add(user)
        db.commit()

        for i in range(3):
            fb = Feedback(
                id=str(uuid.uuid4()),
                user_id=user.uid,
                recipe_id=f"recipe-{i}",
                feedback_type="cooked",
                tags=["#揚げ物"],
                rating=3,
            )
            db.add(fb)
        db.flush()

        # created_at を8日前に書き換える
        old_cutoff = datetime.now(timezone.utc) - timedelta(days=8)
        db.query(Feedback).filter(Feedback.user_id == user.uid).update(
            {"created_at": old_cutoff}
        )
        db.commit()

        result = get_nutrition_adjustment_suggestion(user, db)
        assert result is None


# ============================================================
# get_calendar_meal_prep_suggestion のテスト
# ============================================================

class TestGetCalendarMealPrepSuggestion:
    """作り置き提案（スタブ）のテスト。"""

    def test_returns_none_always(self):
        """スタブ実装のため常に None を返すこと。"""
        user = make_user()
        result = get_calendar_meal_prep_suggestion(user)
        assert result is None


# ============================================================
# /api/proactive エンドポイントのテスト
# ============================================================

class TestProactiveEndpoint:
    """GET /api/proactive エンドポイントのテスト。"""

    def test_requires_authentication(self, client):
        """認証なしのリクエストは 401 を返すこと。"""
        res = client.get("/api/proactive")
        assert res.status_code == 401

    def test_returns_empty_list_when_no_triggers(self, client, auth_headers):
        """トリガーが発火しない場合は空の suggestions リストを返すこと。"""
        res = client.get("/api/proactive", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert "suggestions" in body
        assert isinstance(body["suggestions"], list)
        # デフォルトユーザーはingredientsもFBもないので空リスト
        assert body["suggestions"] == []

    def test_returns_expiring_suggestion_when_ingredients_expiring(
        self, client, auth_headers, test_user, db
    ):
        """期限切れ間近の食材がある場合に expiring トリガーの提案が含まれること。"""
        # test_user の preferences に期限近い食材を追加
        test_user.preferences = {
            "allergies": [],
            "dislikes": [],
            "goal": "other",
            "kitchen_tools": [],
            "ingredients": [
                {
                    "name": "ほうれん草",
                    "expiry_date": future_date(1),
                    "unit": "束",
                    "quantity": 1,
                }
            ],
        }
        db.commit()

        res = client.get("/api/proactive", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["suggestions"]) >= 1
        trigger_types = [s["trigger_type"] for s in body["suggestions"]]
        assert "expiring" in trigger_types

    def test_suggestion_has_required_fields(self, client, auth_headers, test_user, db):
        """各提案オブジェクトに必須フィールドが存在すること。"""
        test_user.preferences = {
            "allergies": [],
            "dislikes": [],
            "goal": "other",
            "kitchen_tools": [],
            "ingredients": [
                {"name": "卵", "expiry_date": future_date(2), "unit": "個"},
            ],
        }
        db.commit()

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

    def test_suggest_request_is_valid_in_response(self, client, auth_headers, test_user, db):
        """レスポンスの suggest_request が SuggestRequest として有効な構造であること。"""
        test_user.preferences = {
            "allergies": [],
            "dislikes": [],
            "goal": "other",
            "kitchen_tools": [],
            "ingredients": [
                {"name": "豆腐", "expiry_date": future_date(1), "unit": "丁"},
            ],
        }
        db.commit()

        res = client.get("/api/proactive", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert len(body["suggestions"]) >= 1

        sr = body["suggestions"][0]["suggest_request"]
        # SuggestRequest の必須フィールドが存在すること
        assert "cooking_time" in sr
        assert "effort_level" in sr
        assert "mood_tags" in sr
        assert "mood_freetext" in sr
        assert isinstance(sr["cooking_time"], int)

    def test_returns_nutrition_suggestion_when_fried_food_feedback_exists(
        self, client, auth_headers, test_user, db
    ):
        """揚げ物フィードバックが2回以上ある場合に nutrition トリガーの提案が返ること。"""
        for i in range(3):
            fb = Feedback(
                id=str(uuid.uuid4()),
                user_id=test_user.uid,
                recipe_id=f"recipe-{i}",
                feedback_type="cooked",
                tags=["#揚げ物"],
                rating=4,
            )
            db.add(fb)
        db.commit()

        res = client.get("/api/proactive", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        trigger_types = [s["trigger_type"] for s in body["suggestions"]]
        assert "nutrition" in trigger_types
