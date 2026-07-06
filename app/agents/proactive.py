"""
Proactive Agent — 能動的な自律提案ロジック（Issue #40 / Epic 6-3）

SPEC.md §1 Tier2 「⑤ 能動的（Proactive）な自律提案」に対応する。

以下の3つのトリガーによる自律提案を生成する：
1. 賞味期限優先（expiring）: 3日以内に期限が来る食材を優先使用した献立を提案
2. 栄養調整（nutrition）: 直近7日のフィードバックタグから栄養傾向を分析し、調整提案
3. 作り置き（calendar）: カレンダー連携での作り置き提案（拡張・現状はスタブ）

設計方針:
- 提案は Human-in-the-loop 前提。エンドポイントは「提案を返す」だけで自動実行しない。
- Health API (#22/#25) は未実装のため、栄養調整はフィードバックタグからの推定で実装。
- 返却される ProactiveSuggestion はオーケストレーターへの入力として使える SuggestRequest を保持する。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from sqlalchemy.orm import Session

from ..models import Feedback, User
from ..schemas import IngredientItem, SuggestRequest


# ============================================================
# 出力型
# ============================================================

@dataclass
class ProactiveSuggestion:
    """能動提案を表すデータクラス。"""

    trigger_type: Literal["expiring", "nutrition", "calendar"]
    """提案のトリガー種別。"""

    suggest_request: SuggestRequest
    """オーケストレーター（/api/suggest / /api/propose）への入力として使える提案リクエスト。"""

    reason: str
    """Human-in-the-loop のための提案理由（ユーザーが承認・修正するための説明文）。"""

    urgency: Literal["high", "medium", "low"]
    """緊急度。高い順: high → medium → low。"""


# ============================================================
# 賞味期限優先提案
# ============================================================

def get_expiring_ingredients_suggestion(
    user: User,
    days_threshold: int = 3,
) -> Optional[ProactiveSuggestion]:
    """
    ユーザーの preferences.ingredients から賞味期限が近い食材を抽出し、
    それを優先使用する SuggestRequest を構築して返す。

    Args:
        user: 対象ユーザー（preferences.ingredients に食材リストが格納されている想定）。
        days_threshold: 期限まで何日以内の食材を「期限切れ間近」とみなすか（デフォルト: 3日）。

    Returns:
        期限切れ間近の食材が存在する場合は ProactiveSuggestion、存在しない場合は None。
    """
    prefs = user.preferences or {}
    ingredients_raw: list = prefs.get("ingredients") or []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_threshold)

    expiring: List[IngredientItem] = []
    for item in ingredients_raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name", "")
        if not name:
            continue
        expiry_str = item.get("expiry_date")
        if not expiry_str:
            continue
        try:
            # ISO 8601形式（"2025-07-09" or "2025-07-09T00:00:00Z" 等）を解析
            expiry_dt_raw = datetime.fromisoformat(str(expiry_str).replace("Z", "+00:00"))
            # タイムゾーン情報がない場合はUTCとして扱う
            if expiry_dt_raw.tzinfo is None:
                expiry_dt = expiry_dt_raw.replace(tzinfo=timezone.utc)
            else:
                expiry_dt = expiry_dt_raw
        except (ValueError, TypeError):
            continue

        # 現在より過去（既に期限切れ）のものも含める（使い切りを促すため）
        if expiry_dt <= cutoff:
            expiring.append(
                IngredientItem(
                    name=name,
                    quantity=item.get("quantity"),
                    unit=item.get("unit", ""),
                    freshness="expiring",
                )
            )

    if not expiring:
        return None

    ingredient_names = "、".join(i.name for i in expiring[:5])
    reason = (
        f"冷蔵庫に賞味期限が近い食材があります（{ingredient_names}）。"
        f"これらを優先して使い切る献立を提案します。食品ロス削減にもつながります。"
    )

    # 3食材以上期限切れ間近であれば高緊急度
    urgency: Literal["high", "medium", "low"] = "high" if len(expiring) >= 3 else "medium"

    suggest_request = SuggestRequest(
        cooking_time=40,
        effort_level="normal",
        mood_tags=[],
        mood_freetext=f"賞味期限が近い食材（{ingredient_names}）を必ず使ってください。食材を無駄にしない献立にしてください。",
        ingredients=expiring,
    )

    return ProactiveSuggestion(
        trigger_type="expiring",
        suggest_request=suggest_request,
        reason=reason,
        urgency=urgency,
    )


# ============================================================
# 栄養調整提案
# ============================================================

# 「不健康傾向」タグとそれに対応する改善方向のマッピング
_UNHEALTHY_TAG_PATTERNS: dict[str, str] = {
    "#揚げ物": "野菜多め・あっさり",
    "#こってり": "野菜多め・あっさり",
    "#肉": "魚・野菜中心",
    "#脂っこい": "野菜多め・さっぱり",
    "#ラーメン": "和食・野菜多め",
    "#カレー": "和食・あっさり",
    "#ジャンク": "バランスの良い和食",
    "#塩辛い": "薄味・野菜多め",
}

_HEALTHY_MOOD_FREETEXT_TEMPLATE = (
    "最近{trend}の料理が続いています。"
    "今日は{adjustment}の献立にしてください。栄養バランスを整えることを優先してください。"
)


def get_nutrition_adjustment_suggestion(
    user: User,
    db: Session,
    days: int = 7,
) -> Optional[ProactiveSuggestion]:
    """
    直近 `days` 日のフィードバックタグから栄養傾向を分析し、調整提案を返す。

    Health API (#22/#25) は未実装のため、フォールバック実装として
    フィードバックタグから不健康傾向を推定する。

    Args:
        user: 対象ユーザー。
        db: SQLAlchemy セッション。
        days: 分析対象の期間（直近何日分のFBを見るか）。

    Returns:
        不健康傾向が検出された場合は ProactiveSuggestion、検出されなかった場合は None。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    recent_feedbacks = (
        db.query(Feedback)
        .filter(
            Feedback.user_id == user.uid,
            Feedback.created_at >= cutoff,
        )
        .all()
    )

    if not recent_feedbacks:
        return None

    # タグの出現頻度を集計
    tag_counts: dict[str, int] = {}
    for fb in recent_feedbacks:
        for tag in fb.tags or []:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # 不健康傾向タグを検出（2回以上出現したもの）
    detected_unhealthy: list[tuple[str, str]] = []
    for tag, adjustment in _UNHEALTHY_TAG_PATTERNS.items():
        if tag_counts.get(tag, 0) >= 2:
            detected_unhealthy.append((tag, adjustment))

    if not detected_unhealthy:
        return None

    # 最も頻度の高い不健康傾向タグを使って提案を構築
    dominant_tag, dominant_adjustment = max(
        detected_unhealthy,
        key=lambda x: tag_counts.get(x[0], 0),
    )
    dominant_count = tag_counts[dominant_tag]

    trend_label = dominant_tag.lstrip("#")
    mood_freetext = _HEALTHY_MOOD_FREETEXT_TEMPLATE.format(
        trend=trend_label,
        adjustment=dominant_adjustment,
    )

    reason = (
        f"直近{days}日のフィードバックを分析すると、{trend_label}の料理が"
        f"{dominant_count}回続いています。"
        f"今日は{dominant_adjustment}の献立で栄養バランスを整えることを提案します。"
    )

    suggest_request = SuggestRequest(
        cooking_time=30,
        effort_level="normal",
        mood_tags=[],
        mood_freetext=mood_freetext,
    )

    return ProactiveSuggestion(
        trigger_type="nutrition",
        suggest_request=suggest_request,
        reason=reason,
        urgency="medium",
    )


# ============================================================
# 作り置き提案（カレンダー連携 — 拡張スタブ）
# ============================================================

def get_calendar_meal_prep_suggestion(
    user: User,
) -> Optional[ProactiveSuggestion]:
    """
    カレンダー連携での作り置き提案（拡張機能）。

    カレンダーAPIは未実装のため、現状は常に None を返すスタブ実装。
    将来的にGoogle Calendar APIを連携させ、「週末の予定が多い日」などを
    検知して作り置き提案を生成する。

    Args:
        user: 対象ユーザー。

    Returns:
        常に None（スタブ実装）。
    """
    # TODO: Google Calendar API 連携実装（拡張チケット）
    return None


# ============================================================
# 統合エントリポイント
# ============================================================

def get_proactive_suggestions(
    user: User,
    db: Session,
) -> List[ProactiveSuggestion]:
    """
    全トリガーを評価し、発火した提案のリストを返す。

    提案は Human-in-the-loop 前提であり、この関数は「提案を生成する」だけで
    自動的にオーケストレーターを呼び出したり、通知を送信したりしない。

    Args:
        user: 対象ユーザー。
        db: SQLAlchemy セッション。

    Returns:
        発火した ProactiveSuggestion のリスト。提案がない場合は空リスト。
    """
    suggestions: List[ProactiveSuggestion] = []

    # 1. 賞味期限優先提案
    expiring_suggestion = get_expiring_ingredients_suggestion(user)
    if expiring_suggestion is not None:
        suggestions.append(expiring_suggestion)

    # 2. 栄養調整提案
    nutrition_suggestion = get_nutrition_adjustment_suggestion(user, db)
    if nutrition_suggestion is not None:
        suggestions.append(nutrition_suggestion)

    # 3. 作り置き提案（スタブ — 常に None）
    calendar_suggestion = get_calendar_meal_prep_suggestion(user)
    if calendar_suggestion is not None:
        suggestions.append(calendar_suggestion)

    return suggestions
