"""
Firestore リポジトリ層。

全コレクション操作をここに集約する。
コレクション設計:
  users/{uid}                        ← ユーザープロファイル
  users/{uid}/feedbacks/{id}         ← フィードバック
  users/{uid}/meal_proposals/{id}    ← 提案履歴
  users/{uid}/recipe_sources/{id}    ← レシピソース
  users/{uid}/notification_settings  ← 通知設定（単一ドキュメント）
  users/{uid}/meal_histories/{id}    ← 食事履歴
  quality_score_logs/{id}            ← 品質スコアログ（ユーザー横断）
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from google.cloud import firestore

_client: Optional[firestore.Client] = None


def get_client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    return _client


# ---------------------------------------------------------------------------
# データクラス（SQLAlchemy モデルの代替）
# ---------------------------------------------------------------------------

class UserDoc:
    def __init__(self, data: dict) -> None:
        self.uid: str = data["uid"]
        self.email: str = data.get("email", "")
        self.hashed_password: Optional[str] = data.get("hashed_password")
        self.display_name: Optional[str] = data.get("display_name")
        self.preferences: dict = data.get("preferences") or {}
        self.created_at: Optional[datetime] = _to_dt(data.get("created_at"))
        self.updated_at: Optional[datetime] = _to_dt(data.get("updated_at"))

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "email": self.email,
            "hashed_password": self.hashed_password,
            "display_name": self.display_name,
            "preferences": self.preferences,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class FeedbackDoc:
    def __init__(self, data: dict) -> None:
        self.id: str = data["id"]
        self.user_id: str = data["user_id"]
        self.recipe_id: str = data["recipe_id"]
        self.recipe_title: Optional[str] = data.get("recipe_title")
        self.feedback_type: str = data["feedback_type"]
        self.tags: list = data.get("tags") or []
        self.rating: Optional[int] = data.get("rating")
        self.comment: Optional[str] = data.get("comment")
        self.nutrition_goal_met: Optional[bool] = data.get("nutrition_goal_met")
        self.created_at: Optional[datetime] = _to_dt(data.get("created_at"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "recipe_id": self.recipe_id,
            "recipe_title": self.recipe_title,
            "feedback_type": self.feedback_type,
            "tags": self.tags,
            "rating": self.rating,
            "comment": self.comment,
            "nutrition_goal_met": self.nutrition_goal_met,
            "created_at": self.created_at,
        }


class MealProposalDoc:
    def __init__(self, data: dict) -> None:
        self.id: str = data["id"]
        self.user_id: str = data["user_id"]
        self.recipe_id: str = data["recipe_id"]
        self.recipe_title: str = data["recipe_title"]
        self.proposed_at: Optional[datetime] = _to_dt(data.get("proposed_at"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "recipe_id": self.recipe_id,
            "recipe_title": self.recipe_title,
            "proposed_at": self.proposed_at,
        }


class RecipeSourceDoc:
    def __init__(self, data: dict) -> None:
        self.id: str = data["id"]
        self.user_id: str = data["user_id"]
        self.url: str = data["url"]
        self.source_type: str = data["source_type"]
        self.title: Optional[str] = data.get("title")
        self.extracted_summary: Optional[dict] = data.get("extracted_summary")
        self.summary_text: Optional[str] = data.get("summary_text")
        self.tags: list = data.get("tags") or []
        self.status: str = data.get("status", "completed")
        self.error_message: Optional[str] = data.get("error_message")
        self.created_at: Optional[datetime] = _to_dt(data.get("created_at"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "url": self.url,
            "source_type": self.source_type,
            "title": self.title,
            "extracted_summary": self.extracted_summary,
            "summary_text": self.summary_text,
            "tags": self.tags,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at,
        }


class NotificationSettingsDoc:
    def __init__(self, data: dict) -> None:
        self.user_id: str = data["user_id"]
        self.enabled: bool = data.get("enabled", True)
        self.breakfast_time: str = data.get("breakfast_time", "07:30")
        self.lunch_time: str = data.get("lunch_time", "11:30")
        self.dinner_time: str = data.get("dinner_time", "17:30")
        self.created_at: Optional[datetime] = _to_dt(data.get("created_at"))
        self.updated_at: Optional[datetime] = _to_dt(data.get("updated_at"))

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "enabled": self.enabled,
            "breakfast_time": self.breakfast_time,
            "lunch_time": self.lunch_time,
            "dinner_time": self.dinner_time,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MealHistoryDoc:
    def __init__(self, data: dict) -> None:
        self.id: str = data["id"]
        self.user_id: str = data["user_id"]
        self.date = data.get("date")
        self.meal_type: str = data.get("meal_type", "")
        self.status: str = data.get("status", "")
        self.recipe: dict = data.get("recipe") or {}
        self.suggested_at: Optional[datetime] = _to_dt(data.get("suggested_at"))
        self.decided_at: Optional[datetime] = _to_dt(data.get("decided_at"))
        self.cooking_started_at: Optional[datetime] = _to_dt(data.get("cooking_started_at"))
        self.cooking_completed_at: Optional[datetime] = _to_dt(data.get("cooking_completed_at"))
        self.ingredients_used: Optional[list] = data.get("ingredients_used")
        self.created_at: Optional[datetime] = _to_dt(data.get("created_at"))


class QualityScoreLogDoc:
    def __init__(self, data: dict) -> None:
        self.id: str = data["id"]
        self.user_id: Optional[str] = data.get("user_id")
        self.subject_type: str = data.get("subject_type", "suggestion")
        self.subject_id: Optional[str] = data.get("subject_id")
        self.score: float = data["score"]
        self.eval_version: Optional[str] = data.get("eval_version")
        self.rationale: Optional[str] = data.get("rationale")
        self.evaluated_at: Optional[datetime] = _to_dt(data.get("evaluated_at"))


def _to_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    # Firestore DatetimeWithNanoseconds など
    try:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    except AttributeError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

def get_user(uid: str) -> Optional[UserDoc]:
    doc = get_client().collection("users").document(uid).get()
    if not doc.exists:
        return None
    return UserDoc(doc.to_dict())


def create_user(uid: str, email: str, display_name: Optional[str], preferences: dict) -> UserDoc:
    now = _now()
    data = {
        "uid": uid,
        "email": email,
        "hashed_password": None,
        "display_name": display_name,
        "preferences": preferences,
        "created_at": now,
        "updated_at": now,
    }
    get_client().collection("users").document(uid).set(data)
    return UserDoc(data)


def update_user(uid: str, display_name: Optional[str] = None, preferences: Optional[dict] = None) -> Optional[UserDoc]:
    updates: dict = {"updated_at": _now()}
    if display_name is not None:
        updates["display_name"] = display_name
    if preferences is not None:
        updates["preferences"] = preferences
    ref = get_client().collection("users").document(uid)
    ref.update(updates)
    return get_user(uid)


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

def save_feedback(fb: FeedbackDoc) -> FeedbackDoc:
    if fb.created_at is None:
        fb.created_at = _now()
    get_client().collection("users").document(fb.user_id).collection("feedbacks").document(fb.id).set(fb.to_dict())
    return fb


def get_feedbacks(user_id: str) -> list[FeedbackDoc]:
    docs = get_client().collection("users").document(user_id).collection("feedbacks").stream()
    return [FeedbackDoc(d.to_dict()) for d in docs]


def get_feedbacks_since(user_id: str, since: datetime) -> list[FeedbackDoc]:
    docs = (
        get_client().collection("users").document(user_id).collection("feedbacks")
        .where("created_at", ">=", since)
        .stream()
    )
    return [FeedbackDoc(d.to_dict()) for d in docs]


def get_feedbacks_with_comment(user_id: str) -> list[FeedbackDoc]:
    """自由記述FB（cooked かつ comment あり）を取得する。"""
    docs = (
        get_client().collection("users").document(user_id).collection("feedbacks")
        .where("feedback_type", "==", "cooked")
        .stream()
    )
    return [FeedbackDoc(d.to_dict()) for d in docs if (d.to_dict().get("comment") or "").strip()]


# ---------------------------------------------------------------------------
# MealProposal
# ---------------------------------------------------------------------------

def save_meal_proposals(user_id: str, proposals: list[MealProposalDoc]) -> None:
    batch = get_client().batch()
    col = get_client().collection("users").document(user_id).collection("meal_proposals")
    for p in proposals:
        if p.proposed_at is None:
            p.proposed_at = _now()
        batch.set(col.document(p.id), p.to_dict())
    batch.commit()


def get_meal_proposals_since(user_id: str, since: datetime) -> list[MealProposalDoc]:
    docs = (
        get_client().collection("users").document(user_id).collection("meal_proposals")
        .where("proposed_at", ">=", since)
        .stream()
    )
    return [MealProposalDoc(d.to_dict()) for d in docs]


# ---------------------------------------------------------------------------
# RecipeSource
# ---------------------------------------------------------------------------

def save_recipe_source(src: RecipeSourceDoc) -> RecipeSourceDoc:
    if src.created_at is None:
        src.created_at = _now()
    get_client().collection("users").document(src.user_id).collection("recipe_sources").document(src.id).set(src.to_dict())
    return src


def get_recipe_sources_completed(user_id: str) -> list[RecipeSourceDoc]:
    docs = (
        get_client().collection("users").document(user_id).collection("recipe_sources")
        .where("status", "==", "completed")
        .stream()
    )
    return [RecipeSourceDoc(d.to_dict()) for d in docs]


# ---------------------------------------------------------------------------
# NotificationSettings
# ---------------------------------------------------------------------------

_NOTIF_DOC = "notification_settings"


def get_notification_settings(user_id: str) -> Optional[NotificationSettingsDoc]:
    doc = get_client().collection("users").document(user_id).collection("settings").document(_NOTIF_DOC).get()
    if not doc.exists:
        return None
    return NotificationSettingsDoc(doc.to_dict())


def get_or_create_notification_settings(user_id: str) -> NotificationSettingsDoc:
    existing = get_notification_settings(user_id)
    if existing:
        return existing
    now = _now()
    data = {
        "user_id": user_id,
        "enabled": True,
        "breakfast_time": "07:30",
        "lunch_time": "11:30",
        "dinner_time": "17:30",
        "created_at": now,
        "updated_at": now,
    }
    get_client().collection("users").document(user_id).collection("settings").document(_NOTIF_DOC).set(data)
    return NotificationSettingsDoc(data)


def update_notification_settings(user_id: str, **kwargs: Any) -> NotificationSettingsDoc:
    kwargs["updated_at"] = _now()
    get_client().collection("users").document(user_id).collection("settings").document(_NOTIF_DOC).update(kwargs)
    return get_or_create_notification_settings(user_id)


# ---------------------------------------------------------------------------
# MealHistory
# ---------------------------------------------------------------------------

def get_meal_histories_with_ingredients(user_id: str) -> list[MealHistoryDoc]:
    docs = (
        get_client().collection("users").document(user_id).collection("meal_histories")
        .stream()
    )
    result = []
    for d in docs:
        data = d.to_dict()
        if data.get("ingredients_used") is not None:
            result.append(MealHistoryDoc(data))
    return result


def get_meal_histories_with_timing(user_id: str) -> list[MealHistoryDoc]:
    docs = get_client().collection("users").document(user_id).collection("meal_histories").stream()
    return [MealHistoryDoc(d.to_dict()) for d in docs]


# ---------------------------------------------------------------------------
# QualityScoreLog
# ---------------------------------------------------------------------------

def get_quality_score_logs(user_id: Optional[str], limit: int = 90) -> list[QualityScoreLogDoc]:
    query = get_client().collection("quality_score_logs").order_by("evaluated_at")
    if user_id is not None:
        # Firestore は OR クエリをネイティブサポートしないため全件取得してフィルタ
        docs = list(query.stream())
        result = []
        for d in docs:
            data = d.to_dict()
            if data.get("user_id") == user_id or data.get("user_id") is None:
                result.append(QualityScoreLogDoc(data))
        return result[:limit]
    return [QualityScoreLogDoc(d.to_dict()) for d in query.limit(limit).stream()]
