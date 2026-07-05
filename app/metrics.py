"""
Issue #37: アウトカム・ダッシュボード — 指標算出ロジック

Output ではなく Outcome / Impact を実測値で可視化する。
現時点では以下の前提データがまだ蓄積されていない可能性が高い：
  - 提案履歴管理（#24）
  - フィードバック機能（#23）
  - LLM-as-judge eval基盤（#34）

このモジュールは「データが揃った際に正しく動く」算出ロジックを実装しつつ、
データが不足している間は誠実に `has_data=False` / `None` を返し、
UIが「データ蓄積中」であることを表示できるようにする。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Feedback, MealHistory, QualityScoreLog


# ==========================================
# 1. 食品ロス削減率（食材使い切り率）
# ==========================================

def calc_food_waste_reduction_rate(db: Session, user_id: str) -> dict:
    """
    MealHistory.ingredients_used に記録された「使用食材」のうち、
    賞味期限が近かった食材（was_expiring=True）をどれだけ使い切れたかの比率。

    算出ロジック:
      食品ロス削減率 = 期限が近かった食材のうち実際に使用された食材数 / 記録された食材の総数

    データが無い場合（ingredients_used が誰にも記録されていない）は
    has_data=False, value=None を返す。
    """
    histories = (
        db.query(MealHistory)
        .filter(MealHistory.user_id == user_id)
        .filter(MealHistory.ingredients_used.isnot(None))
        .all()
    )

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


# ==========================================
# 2. 栄養目標達成率
# ==========================================

def calc_nutrition_goal_achievement_rate(db: Session, user_id: str) -> dict:
    """
    Feedback.nutrition_goal_met（ユーザー自己申告 or 将来の栄養API連携判定）の集計。

    算出ロジック:
      栄養目標達成率 = nutrition_goal_met=True の件数 / nutrition_goal_met が記録された件数
    """
    total = (
        db.query(func.count(Feedback.id))
        .filter(Feedback.user_id == user_id)
        .filter(Feedback.nutrition_goal_met.isnot(None))
        .scalar()
    ) or 0

    if total == 0:
        return {
            "has_data": False,
            "value": None,
            "unit": "percent",
            "sample_size": 0,
            "description": "栄養目標達成率（自己申告・栄養API連携による判定の割合）",
        }

    achieved = (
        db.query(func.count(Feedback.id))
        .filter(Feedback.user_id == user_id)
        .filter(Feedback.nutrition_goal_met.is_(True))
        .scalar()
    ) or 0

    rate = round((achieved / total) * 100, 1)
    return {
        "has_data": True,
        "value": rate,
        "unit": "percent",
        "sample_size": total,
        "description": "栄養目標達成率（自己申告・栄養API連携による判定の割合）",
    }


# ==========================================
# 3. 献立決定時間の短縮 / 調理時間削減
# ==========================================

def calc_decision_time_seconds(db: Session, user_id: str) -> dict:
    """
    MealHistory.suggested_at 〜 decided_at の平均経過秒数。
    「献立決定時間」＝AIが提案を返してからユーザーが確定するまでの時間。
    """
    histories = (
        db.query(MealHistory)
        .filter(MealHistory.user_id == user_id)
        .filter(MealHistory.suggested_at.isnot(None))
        .filter(MealHistory.decided_at.isnot(None))
        .all()
    )

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


def calc_cooking_time_seconds(db: Session, user_id: str) -> dict:
    """
    MealHistory.cooking_started_at 〜 cooking_completed_at の平均調理時間（秒）。
    """
    histories = (
        db.query(MealHistory)
        .filter(MealHistory.user_id == user_id)
        .filter(MealHistory.cooking_started_at.isnot(None))
        .filter(MealHistory.cooking_completed_at.isnot(None))
        .all()
    )

    durations = [
        (h.cooking_completed_at - h.cooking_started_at).total_seconds()
        for h in histories
        if h.cooking_completed_at and h.cooking_started_at and h.cooking_completed_at >= h.cooking_started_at
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


# ==========================================
# 4. 提案品質スコア（LLM-as-judge）の推移
# ==========================================

def calc_quality_score_trend(db: Session, user_id: Optional[str] = None, limit: int = 90) -> dict:
    """
    QualityScoreLog の時系列データを返す。
    LLM-as-judge eval基盤（#34）が未実装のため、現状は0件が正常系。
    その場合は has_data=False, points=[] を返し、UIは空グラフを表示する。
    """
    query = db.query(QualityScoreLog).order_by(QualityScoreLog.evaluated_at.asc())
    if user_id is not None:
        query = query.filter(
            (QualityScoreLog.user_id == user_id) | (QualityScoreLog.user_id.is_(None))
        )
    logs = query.limit(limit).all()

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


# ==========================================
# まとめて取得
# ==========================================

def build_metrics_response(db: Session, user_id: str) -> dict:
    return {
        "food_waste_reduction_rate": calc_food_waste_reduction_rate(db, user_id),
        "nutrition_goal_achievement_rate": calc_nutrition_goal_achievement_rate(db, user_id),
        "decision_time": calc_decision_time_seconds(db, user_id),
        "cooking_time": calc_cooking_time_seconds(db, user_id),
        "quality_score_trend": calc_quality_score_trend(db, user_id),
    }
