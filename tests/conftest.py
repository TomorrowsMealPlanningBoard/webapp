"""
テスト共通の fixture。
Firestore への実際のアクセスはインメモリ辞書でモックする。
"""
import sys
import os
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import patch

# pyproject.toml の pythonpath 設定が効かない環境（CI等）でも app を解決できるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# app.auth はモジュールロード時に JWT_SECRET_KEY を評価するため、import より前に設定する
if not os.environ.get("JWT_SECRET_KEY"):
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-unit-tests-only"

import pytest
from fastapi.testclient import TestClient

from app.firestore_store import (
    UserDoc, FeedbackDoc, MealProposalDoc, RecipeSourceDoc,
    MealHistoryDoc, QualityScoreLogDoc, NotificationSettingsDoc,
)
from app.auth import create_access_token


# ---------------------------------------------------------------------------
# インメモリ Firestore ストア（テスト用）
# ---------------------------------------------------------------------------

class InMemoryStore:
    """テスト用のインメモリデータストア。各テストで fresh なインスタンスを使う。"""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self.feedbacks: dict[str, list[dict]] = {}          # user_id -> list
        self.meal_proposals: dict[str, list[dict]] = {}     # user_id -> list
        self.recipe_sources: dict[str, list[dict]] = {}     # user_id -> list
        self.notification_settings: dict[str, dict] = {}   # user_id -> dict
        self.meal_histories: dict[str, list[dict]] = {}     # user_id -> list
        self.quality_score_logs: list[dict] = []

    def add_user(self, uid: str, email: str, display_name: str = "テストユーザー", preferences: dict = None):
        self.users[uid] = {
            "uid": uid,
            "email": email,
            "display_name": display_name,
            "hashed_password": None,
            "preferences": preferences or {"allergies": [], "dislikes": [], "goal": "other", "kitchen_tools": []},
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        return UserDoc(self.users[uid])

    def add_feedback(self, user_id: str, id: str, recipe_id: str, feedback_type: str,
                     tags: list = None, rating: int = None, comment: str = None,
                     nutrition_goal_met: bool = None, created_at: datetime = None):
        fb = {
            "id": id, "user_id": user_id, "recipe_id": recipe_id,
            "feedback_type": feedback_type,
            "tags": tags or [], "rating": rating, "comment": comment,
            "nutrition_goal_met": nutrition_goal_met,
            "created_at": created_at or datetime.now(timezone.utc),
        }
        self.feedbacks.setdefault(user_id, []).append(fb)
        return FeedbackDoc(fb)

    def add_meal_proposal(self, user_id: str, id: str, recipe_id: str, recipe_title: str,
                          proposed_at: datetime = None):
        mp = {
            "id": id, "user_id": user_id, "recipe_id": recipe_id,
            "recipe_title": recipe_title,
            "proposed_at": proposed_at or datetime.now(timezone.utc),
        }
        self.meal_proposals.setdefault(user_id, []).append(mp)
        return MealProposalDoc(mp)

    def add_recipe_source(self, user_id: str, id: str, url: str = "https://example.com/recipe",
                          source_type: str = "blog", title: str = "テストレシピ記事",
                          summary_text: str = "", extracted_summary: dict = None,
                          tags: list = None, status: str = "completed"):
        rs = {
            "id": id, "user_id": user_id, "url": url, "source_type": source_type,
            "title": title, "summary_text": summary_text,
            "extracted_summary": extracted_summary or {"seasoning_tendency": summary_text},
            "tags": tags or [], "status": status,
            "created_at": datetime.now(timezone.utc),
        }
        self.recipe_sources.setdefault(user_id, []).append(rs)
        return RecipeSourceDoc(rs)

    def add_meal_history(self, user_id: str, id: str, meal_type: str = "dinner",
                         status: str = "completed", recipe: dict = None,
                         ingredients_used: list = None,
                         suggested_at: datetime = None, decided_at: datetime = None,
                         cooking_started_at: datetime = None, cooking_completed_at: datetime = None):
        mh = {
            "id": id, "user_id": user_id, "meal_type": meal_type, "status": status,
            "recipe": recipe or {}, "ingredients_used": ingredients_used,
            "suggested_at": suggested_at, "decided_at": decided_at,
            "cooking_started_at": cooking_started_at, "cooking_completed_at": cooking_completed_at,
            "created_at": datetime.now(timezone.utc),
        }
        self.meal_histories.setdefault(user_id, []).append(mh)
        return MealHistoryDoc(mh)

    def add_quality_score_log(self, id: str, score: float, user_id: str = None,
                               subject_type: str = "suggestion", subject_id: str = None,
                               eval_version: str = None, evaluated_at: datetime = None):
        qs = {
            "id": id, "user_id": user_id, "subject_type": subject_type,
            "subject_id": subject_id, "score": score, "eval_version": eval_version,
            "evaluated_at": evaluated_at or datetime.now(timezone.utc),
        }
        self.quality_score_logs.append(qs)
        return QualityScoreLogDoc(qs)


# ---------------------------------------------------------------------------
# firestore_store モック関数を InMemoryStore から生成
# ---------------------------------------------------------------------------

def _build_store_mocks(store: InMemoryStore):
    """InMemoryStore を参照するモック関数群を返す。"""
    from datetime import timedelta

    def get_user(uid: str) -> Optional[UserDoc]:
        d = store.users.get(uid)
        return UserDoc(d) if d else None

    def create_user(uid: str, email: str, display_name: Optional[str] = None,
                    preferences: dict = None) -> UserDoc:
        d = {
            "uid": uid, "email": email, "display_name": display_name,
            "hashed_password": None, "preferences": preferences or {},
            "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
        }
        store.users[uid] = d
        return UserDoc(d)

    def update_user(uid: str, updates: dict) -> None:
        if uid in store.users:
            store.users[uid].update(updates)

    def save_feedback(fb_or_user_id, data: dict = None) -> FeedbackDoc:
        if isinstance(fb_or_user_id, str):
            # 旧シグネチャ: save_feedback(user_id, data)
            fb = {"user_id": fb_or_user_id, "created_at": datetime.now(timezone.utc), **(data or {})}
        else:
            # 新シグネチャ: save_feedback(FeedbackDoc)
            fb_doc = fb_or_user_id
            fb = fb_doc.to_dict() if hasattr(fb_doc, "to_dict") else vars(fb_doc)
        store.feedbacks.setdefault(fb["user_id"], []).append(fb)
        return FeedbackDoc(fb)

    def get_feedbacks(user_id: str) -> list:
        return [FeedbackDoc(d) for d in store.feedbacks.get(user_id, [])]

    def get_feedbacks_since(user_id: str, since: datetime) -> list:
        return [FeedbackDoc(d) for d in store.feedbacks.get(user_id, [])
                if d.get("created_at") and d["created_at"] >= since]

    def get_feedbacks_with_comment(user_id: str) -> list:
        return [FeedbackDoc(d) for d in store.feedbacks.get(user_id, [])
                if d.get("comment") and d["comment"].strip()
                and d.get("feedback_type") == "cooked"]

    def save_meal_proposals(user_id: str, proposals: list) -> None:
        for p in proposals:
            if hasattr(p, "to_dict"):
                d = p.to_dict()
            elif isinstance(p, dict):
                d = p
            else:
                d = {"id": str(p), "user_id": user_id}
            d.setdefault("user_id", user_id)
            if not d.get("proposed_at"):
                d["proposed_at"] = datetime.now(timezone.utc)
            store.meal_proposals.setdefault(user_id, []).append(d)

    def get_meal_proposals_since(user_id: str, since: datetime) -> list:
        return [MealProposalDoc(d) for d in store.meal_proposals.get(user_id, [])
                if d.get("proposed_at") and d["proposed_at"] >= since]

    def save_recipe_source(src_or_user_id, data: dict = None) -> RecipeSourceDoc:
        if isinstance(src_or_user_id, str):
            # 旧シグネチャ
            d = {"user_id": src_or_user_id, "created_at": datetime.now(timezone.utc), **(data or {})}
        else:
            # 新シグネチャ: save_recipe_source(RecipeSourceDoc)
            src = src_or_user_id
            d = src.to_dict() if hasattr(src, "to_dict") else vars(src)
        d.setdefault("created_at", datetime.now(timezone.utc))
        store.recipe_sources.setdefault(d["user_id"], []).append(d)
        return RecipeSourceDoc(d)

    def get_recipe_sources_completed(user_id: str) -> list:
        return [RecipeSourceDoc(d) for d in store.recipe_sources.get(user_id, [])
                if d.get("status") == "completed"]

    def get_or_create_notification_settings(user_id: str) -> NotificationSettingsDoc:
        if user_id not in store.notification_settings:
            store.notification_settings[user_id] = {
                "user_id": user_id, "enabled": True,
                "breakfast_time": "07:30", "lunch_time": "11:30", "dinner_time": "17:30",
            }
        return NotificationSettingsDoc(store.notification_settings[user_id])

    def update_notification_settings(user_id: str, **kwargs) -> NotificationSettingsDoc:
        if user_id not in store.notification_settings:
            get_or_create_notification_settings(user_id)
        store.notification_settings[user_id].update(kwargs)
        return NotificationSettingsDoc(store.notification_settings[user_id])

    def get_meal_histories_with_ingredients(user_id: str) -> list:
        return [MealHistoryDoc(d) for d in store.meal_histories.get(user_id, [])
                if d.get("ingredients_used") is not None]

    def get_meal_histories_with_timing(user_id: str) -> list:
        return [MealHistoryDoc(d) for d in store.meal_histories.get(user_id, [])
                if d.get("suggested_at") or d.get("cooking_started_at")]

    def get_quality_score_logs(user_id: Optional[str] = None, limit: int = 90) -> list:
        logs = store.quality_score_logs
        if user_id:
            logs = [l for l in logs if l.get("user_id") == user_id]
        return [QualityScoreLogDoc(d) for d in logs[:limit]]

    return {
        # firestore_store モジュール自体
        "app.firestore_store.get_user": get_user,
        "app.firestore_store.create_user": create_user,
        "app.firestore_store.update_user": update_user,
        "app.firestore_store.save_feedback": save_feedback,
        "app.firestore_store.get_feedbacks": get_feedbacks,
        "app.firestore_store.get_feedbacks_since": get_feedbacks_since,
        "app.firestore_store.get_feedbacks_with_comment": get_feedbacks_with_comment,
        "app.firestore_store.save_meal_proposals": save_meal_proposals,
        "app.firestore_store.get_meal_proposals_since": get_meal_proposals_since,
        "app.firestore_store.save_recipe_source": save_recipe_source,
        "app.firestore_store.get_recipe_sources_completed": get_recipe_sources_completed,
        "app.firestore_store.get_or_create_notification_settings": get_or_create_notification_settings,
        "app.firestore_store.update_notification_settings": update_notification_settings,
        "app.firestore_store.get_meal_histories_with_ingredients": get_meal_histories_with_ingredients,
        "app.firestore_store.get_meal_histories_with_timing": get_meal_histories_with_timing,
        "app.firestore_store.get_quality_score_logs": get_quality_score_logs,
        # app.main が from .firestore_store import ... でバインドした参照
        "app.main.get_user": get_user,
        "app.main.create_user": create_user,
        "app.main.update_user": update_user,
        "app.main.save_feedback": save_feedback,
        "app.main.save_meal_proposals": save_meal_proposals,
        "app.main.get_meal_proposals_since": get_meal_proposals_since,
        "app.main.save_recipe_source": save_recipe_source,
        "app.main.get_or_create_notification_settings": get_or_create_notification_settings,
        "app.main.update_notification_settings": update_notification_settings,
        # app.auth
        "app.auth.get_user": get_user,
        # app.metrics
        "app.metrics.get_feedbacks": get_feedbacks,
        "app.metrics.get_meal_histories_with_ingredients": get_meal_histories_with_ingredients,
        "app.metrics.get_meal_histories_with_timing": get_meal_histories_with_timing,
        "app.metrics.get_quality_score_logs": get_quality_score_logs,
        # app.agents.context_retriever
        "app.agents.context_retriever.get_user": get_user,
        "app.agents.context_retriever.get_feedbacks_with_comment": get_feedbacks_with_comment,
        "app.agents.context_retriever.get_meal_proposals_since": get_meal_proposals_since,
        "app.agents.context_retriever.get_recipe_sources_completed": get_recipe_sources_completed,
        # app.agents.structured_store
        "app.agents.structured_store.get_user": get_user,
        "app.agents.structured_store.get_feedbacks": get_feedbacks,
        # app.agents.proactive
        "app.agents.proactive.get_feedbacks_since": get_feedbacks_since,
    }


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def store():
    """テストごとに fresh なインメモリストアを提供する。"""
    return InMemoryStore()


@pytest.fixture(scope="function")
def mock_firestore(store):
    """firestore_store の全関数をインメモリ実装に差し替える。"""
    mocks = _build_store_mocks(store)
    patchers = [patch(target, side_effect=fn) for target, fn in mocks.items()]
    for p in patchers:
        p.start()
    yield store
    for p in patchers:
        p.stop()


@pytest.fixture(scope="function")
def test_user(mock_firestore):
    return mock_firestore.add_user(
        uid="test-user-001",
        email="test@example.com",
        display_name="テストユーザー",
        preferences={
            "allergies": [],
            "dislikes": [],
            "goal": "other",
            "kitchen_tools": [],
        },
    )


@pytest.fixture(scope="function")
def auth_headers(test_user):
    token = create_access_token(data={"sub": test_user.uid})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="function")
def client(mock_firestore):
    from app.main import app, limiter
    from app.daily_limit import reset_for_test as reset_daily_limit
    limiter.enabled = False
    limiter.reset()
    reset_daily_limit()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
