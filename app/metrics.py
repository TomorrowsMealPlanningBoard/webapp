"""
Issue #37: アウトカム・ダッシュボード — 指標算出ロジック（Firestore 実装）
"""
from __future__ import annotations

from typing import Optional

from .firestore_store import (
    get_feedbacks,
    get_meal_histories_with_ingredients,
    get_meal_histories_with_timing,
    get_quality_score_logs,
)


def calc_food_waste_reduction_rate(user_id: str) -> dict:
    histories = get_meal_histories_with_ingredients(user_id)

    total_used = 0
    expiring_used = 0
    for h in histories:
        items = h.ingredients_used or []
        for item in items:
            if not isinstance(item, dict):
                continue
            total_used += 1
            if item.get("was_expiring"):
                expiring_used += 1

    if total_used == 0:
        return {
            "has_data": False,
            "value": None,
            "unit": "percent",
            "sample_size": 0,
            "description": "食材使い切り率（賞味期限が近い食材のうち実際に使用された割合）",
        }

    rate = round((expiring_used / total_used) * 100, 1)
    return {
        "has_data": True,
        "value": rate,
        "unit": "percent",
        "sample_size": total_used,
        "description": "食材使い切り率（賞味期限が近い食材のうち実際に使用された割合）",
    }


def calc_nutrition_goal_achievement_rate(user_id: str) -> dict:
    feedbacks = get_feedbacks(user_id)
    eligible = [fb for fb in feedbacks if fb.nutrition_goal_met is not None]
    total = len(eligible)

    if total == 0:
        return {
            "has_data": False,
            "value": None,
            "unit": "percent",
            "sample_size": 0,
            "description": "栄養目標達成率（自己申告・栄養API連携による判定の割合）",
        }

    achieved = sum(1 for fb in eligible if fb.nutrition_goal_met is True)
    rate = round((achieved / total) * 100, 1)
    return {
        "has_data": True,
        "value": rate,
        "unit": "percent",
        "sample_size": total,
        "description": "栄養目標達成率（自己申告・栄養API連携による判定の割合）",
    }


def calc_decision_time_seconds(user_id: str) -> dict:
    histories = get_meal_histories_with_timing(user_id)

    durations = [
        (h.decided_at - h.suggested_at).total_seconds()
        for h in histories
        if h.decided_at and h.suggested_at and h.decided_at >= h.suggested_at
    ]

    if not durations:
        return {
            "has_data": False,
            "value": None,
            "unit": "seconds",
            "sample_size": 0,
            "description": "献立決定時間（AI提案の表示からユーザーが決定するまでの平均時間）",
        }

    avg_seconds = round(sum(durations) / len(durations), 1)
    return {
        "has_data": True,
        "value": avg_seconds,
        "unit": "seconds",
        "sample_size": len(durations),
        "description": "献立決定時間（AI提案の表示からユーザーが決定するまでの平均時間）",
    }


def calc_cooking_time_seconds(user_id: str) -> dict:
    histories = get_meal_histories_with_timing(user_id)

    durations = [
        (h.cooking_completed_at - h.cooking_started_at).total_seconds()
        for h in histories
        if h.cooking_completed_at and h.cooking_started_at
        and h.cooking_completed_at >= h.cooking_started_at
    ]

    if not durations:
        return {
            "has_data": False,
            "value": None,
            "unit": "seconds",
            "sample_size": 0,
            "description": "実測調理時間（調理開始から完了までの平均時間）",
        }

    avg_seconds = round(sum(durations) / len(durations), 1)
    return {
        "has_data": True,
        "value": avg_seconds,
        "unit": "seconds",
        "sample_size": len(durations),
        "description": "実測調理時間（調理開始から完了までの平均時間）",
    }


def calc_quality_score_trend(user_id: Optional[str] = None, limit: int = 90) -> dict:
    logs = get_quality_score_logs(user_id, limit)

    if not logs:
        return {
            "has_data": False,
            "points": [],
            "unit": "score",
            "sample_size": 0,
            "description": "提案品質スコア（LLM-as-judge）の推移",
        }

    points = [
        {
            "evaluated_at": log.evaluated_at.isoformat() if log.evaluated_at else None,
            "score": log.score,
            "eval_version": log.eval_version,
            "subject_id": log.subject_id,
        }
        for log in logs
    ]
    avg_score = round(sum(p["score"] for p in points) / len(points), 3)
    return {
        "has_data": True,
        "points": points,
        "average": avg_score,
        "unit": "score",
        "sample_size": len(points),
        "description": "提案品質スコア（LLM-as-judge）の推移",
    }


def build_metrics_response(user_id: str) -> dict:
    return {
        "food_waste_reduction_rate": calc_food_waste_reduction_rate(user_id),
        "nutrition_goal_achievement_rate": calc_nutrition_goal_achievement_rate(user_id),
        "decision_time": calc_decision_time_seconds(user_id),
        "cooking_time": calc_cooking_time_seconds(user_id),
        "quality_score_trend": calc_quality_score_trend(user_id),
    }
