"""
Notification Agent — 食事30分前プッシュ通知スケジューリングロジック（Issue #26 / Epic 6-1）

設計方針:
- LLM呼び出しは不要。時刻ベースのロジックのみ。
- should_notify() が現在時刻と設定時刻を比較し、通知タイミングかどうかを判定する。
- build_notification_payload() が通知コンテンツを組み立てて返す。
- deeplinkは /#suggest?meal={meal_type} 形式。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

MealType = Literal["breakfast", "lunch", "dinner"]

MEAL_TYPE_LABELS: dict[str, str] = {
    "breakfast": "朝食",
    "lunch": "昼食",
    "dinner": "夕食",
}

MEAL_TYPE_EMOJIS: dict[str, str] = {
    "breakfast": "🌅",
    "lunch": "☀️",
    "dinner": "🌙",
}

NOTIFY_BEFORE_MINUTES = 30


@dataclass
class NotificationPayload:
    """プッシュ通知の内容を表すデータクラス。"""

    meal_type: str
    recipe_name: str
    title: str
    body: str
    deeplink_url: str


@dataclass
class NotificationScheduleItem:
    """次の通知スケジュールを表すデータクラス。"""

    meal_type: str
    notify_at: str
    meal_time: str


def _parse_hhmm(time_str: str) -> Optional[tuple[int, int]]:
    """HH:MM 形式の文字列を (hour, minute) のタプルとして返す。パース失敗時は None。"""
    try:
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            return None
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return h, m
    except (ValueError, AttributeError):
        return None


def should_notify(
    meal_type: str,
    meal_time_str: str,
    now: Optional[datetime] = None,
    notify_before_minutes: int = NOTIFY_BEFORE_MINUTES,
) -> bool:
    """
    現在時刻が meal_time_str の notify_before_minutes 分前（±1分の許容）かどうかを判定する。

    Args:
        meal_type: 食事種別（"breakfast" / "lunch" / "dinner"）。ログ用のみ使用。
        meal_time_str: 食事時間（"HH:MM" 形式）。
        now: 現在時刻（テスト時に注入する）。None の場合は datetime.now(timezone.utc)。
        notify_before_minutes: 何分前に通知するか（デフォルト: 30分）。

    Returns:
        通知タイミングであれば True、そうでなければ False。
    """
    parsed = _parse_hhmm(meal_time_str)
    if parsed is None:
        return False

    meal_hour, meal_minute = parsed

    if now is None:
        now = datetime.now(timezone.utc)

    now_local_minutes = now.hour * 60 + now.minute
    meal_total_minutes = meal_hour * 60 + meal_minute
    notify_total_minutes = meal_total_minutes - notify_before_minutes

    if notify_total_minutes < 0:
        notify_total_minutes += 24 * 60

    return now_local_minutes == notify_total_minutes


def build_notification_payload(
    meal_type: str,
    recipe_name: str,
) -> NotificationPayload:
    """
    通知コンテンツを組み立てて NotificationPayload を返す。

    Args:
        meal_type: 食事種別（"breakfast" / "lunch" / "dinner"）。
        recipe_name: 提案するレシピ名。

    Returns:
        NotificationPayload。
    """
    label = MEAL_TYPE_LABELS.get(meal_type, meal_type)
    emoji = MEAL_TYPE_EMOJIS.get(meal_type, "🍽️")

    title = f"{emoji} {label}の提案があります"
    body = f"今日の{label}は「{recipe_name}」はいかがですか？"
    deeplink_url = f"/#suggest?meal={meal_type}"

    return NotificationPayload(
        meal_type=meal_type,
        recipe_name=recipe_name,
        title=title,
        body=body,
        deeplink_url=deeplink_url,
    )


def get_next_schedule(
    breakfast_time: str,
    lunch_time: str,
    dinner_time: str,
    notify_before_minutes: int = NOTIFY_BEFORE_MINUTES,
) -> list[NotificationScheduleItem]:
    """
    各食事の通知タイミング（食事時間の notify_before_minutes 分前）のリストを返す。

    Args:
        breakfast_time: 朝食の時刻（"HH:MM" 形式）。
        lunch_time: 昼食の時刻（"HH:MM" 形式）。
        dinner_time: 夕食の時刻（"HH:MM" 形式）。
        notify_before_minutes: 何分前に通知するか。

    Returns:
        NotificationScheduleItem のリスト。パースできない時刻はスキップ。
    """
    items: list[NotificationScheduleItem] = []
    meal_configs: list[tuple[str, str]] = [
        ("breakfast", breakfast_time),
        ("lunch", lunch_time),
        ("dinner", dinner_time),
    ]

    for meal_type, meal_time_str in meal_configs:
        parsed = _parse_hhmm(meal_time_str)
        if parsed is None:
            continue
        meal_hour, meal_minute = parsed
        notify_total = meal_hour * 60 + meal_minute - notify_before_minutes
        if notify_total < 0:
            notify_total += 24 * 60
        notify_h = notify_total // 60
        notify_m = notify_total % 60
        items.append(
            NotificationScheduleItem(
                meal_type=meal_type,
                notify_at=f"{notify_h:02d}:{notify_m:02d}",
                meal_time=meal_time_str,
            )
        )

    return items
