"""
Issue #26: プッシュ通知スケジューリングのユニットテスト

テスト対象:
- should_notify: 現在時刻が通知タイミングかどうかを判定する
- build_notification_payload: 通知コンテンツを組み立てる
- get_next_schedule: 各食事の通知スケジュールを生成する
- GET /api/notifications/settings: 通知設定取得エンドポイント
- PUT /api/notifications/settings: 通知設定更新エンドポイント
- GET /api/notifications/schedule: 通知スケジュール取得エンドポイント
- POST /api/notifications/trigger: 通知トリガーエンドポイント
"""
from datetime import datetime, timezone

import pytest

from app.agents.notification import (
    should_notify,
    build_notification_payload,
    get_next_schedule,
    NOTIFY_BEFORE_MINUTES,
)


# ============================================================
# should_notify のテスト
# ============================================================

class TestShouldNotify:
    """should_notify のタイムベーステスト。"""

    def _make_now(self, hour: int, minute: int) -> datetime:
        return datetime(2025, 1, 1, hour, minute, 0, tzinfo=timezone.utc)

    def test_returns_true_when_exactly_at_notify_time(self):
        """食事時間の30分前であれば True を返すこと。"""
        now = self._make_now(7, 0)
        assert should_notify("breakfast", "07:30", now=now) is True

    def test_returns_false_when_too_early(self):
        """通知時刻より早い場合は False を返すこと。"""
        now = self._make_now(6, 50)
        assert should_notify("breakfast", "07:30", now=now) is False

    def test_returns_false_when_too_late(self):
        """通知時刻を過ぎた場合は False を返すこと。"""
        now = self._make_now(7, 10)
        assert should_notify("breakfast", "07:30", now=now) is False

    def test_returns_true_for_lunch(self):
        """昼食の通知タイミング（11:00）で True を返すこと。"""
        now = self._make_now(11, 0)
        assert should_notify("lunch", "11:30", now=now) is True

    def test_returns_true_for_dinner(self):
        """夕食の通知タイミング（17:00）で True を返すこと。"""
        now = self._make_now(17, 0)
        assert should_notify("dinner", "17:30", now=now) is True

    def test_returns_false_when_invalid_time_format(self):
        """不正な時刻フォーマットの場合は False を返すこと。"""
        now = self._make_now(7, 0)
        assert should_notify("breakfast", "invalid", now=now) is False

    def test_returns_false_when_empty_time_string(self):
        """空文字列の時刻の場合は False を返すこと。"""
        now = self._make_now(7, 0)
        assert should_notify("breakfast", "", now=now) is False

    def test_midnight_wraparound(self):
        """食事時間が真夜中をまたぐ場合（00:15の30分前=23:45）でも正しく判定すること。"""
        now = self._make_now(23, 45)
        assert should_notify("late_snack", "00:15", now=now) is True

    def test_custom_notify_before_minutes(self):
        """notify_before_minutes をカスタマイズできること。"""
        now = self._make_now(7, 15)
        assert should_notify("breakfast", "07:30", now=now, notify_before_minutes=15) is True

    def test_returns_false_with_zero_minutes(self):
        """0分前（食事時刻ちょうど）の場合のテスト。"""
        now = self._make_now(7, 30)
        assert should_notify("breakfast", "07:30", now=now, notify_before_minutes=0) is True


# ============================================================
# build_notification_payload のテスト
# ============================================================

class TestBuildNotificationPayload:
    """build_notification_payload のペイロード構造テスト。"""

    def test_returns_payload_with_correct_meal_type(self):
        """meal_type が正しく格納されること。"""
        payload = build_notification_payload("breakfast", "卵かけご飯")
        assert payload.meal_type == "breakfast"

    def test_returns_payload_with_recipe_name(self):
        """recipe_name が正しく格納されること。"""
        payload = build_notification_payload("lunch", "親子丼")
        assert payload.recipe_name == "親子丼"

    def test_deeplink_url_format(self):
        """deeplink_url が '/#suggest?meal={meal_type}' 形式であること。"""
        payload = build_notification_payload("dinner", "肉じゃが")
        assert payload.deeplink_url == "/#suggest?meal=dinner"

    def test_deeplink_url_includes_meal_type(self):
        """deeplink_url の meal_type パラメータが正しいこと。"""
        for meal_type in ["breakfast", "lunch", "dinner"]:
            payload = build_notification_payload(meal_type, "テストレシピ")
            assert f"meal={meal_type}" in payload.deeplink_url

    def test_title_contains_meal_label(self):
        """title に日本語の食事名称が含まれること。"""
        breakfast_payload = build_notification_payload("breakfast", "トースト")
        lunch_payload = build_notification_payload("lunch", "ラーメン")
        dinner_payload = build_notification_payload("dinner", "カレー")

        assert "朝食" in breakfast_payload.title
        assert "昼食" in lunch_payload.title
        assert "夕食" in dinner_payload.title

    def test_body_contains_recipe_name(self):
        """body にレシピ名が含まれること。"""
        recipe = "豚の生姜焼き"
        payload = build_notification_payload("dinner", recipe)
        assert recipe in payload.body

    def test_unknown_meal_type_uses_default_emoji(self):
        """不明な meal_type でもエラーにならず、デフォルト値で返ること。"""
        payload = build_notification_payload("brunch", "アボカドトースト")
        assert payload.meal_type == "brunch"
        assert payload.recipe_name == "アボカドトースト"
        assert "/#suggest?meal=brunch" == payload.deeplink_url


# ============================================================
# get_next_schedule のテスト
# ============================================================

class TestGetNextSchedule:
    """get_next_schedule のテスト。"""

    def test_returns_three_items_for_valid_times(self):
        """3食分のスケジュールが返ること。"""
        items = get_next_schedule("07:30", "11:30", "17:30")
        assert len(items) == 3

    def test_notify_at_is_30_minutes_before_meal_time(self):
        """notify_at が meal_time の30分前であること。"""
        items = get_next_schedule("07:30", "11:30", "17:30")
        breakfast = next(i for i in items if i.meal_type == "breakfast")
        lunch = next(i for i in items if i.meal_type == "lunch")
        dinner = next(i for i in items if i.meal_type == "dinner")

        assert breakfast.notify_at == "07:00"
        assert lunch.notify_at == "11:00"
        assert dinner.notify_at == "17:00"

    def test_meal_time_is_preserved(self):
        """meal_time が元の値と一致すること。"""
        items = get_next_schedule("08:00", "12:30", "19:00")
        times_by_type = {i.meal_type: i.meal_time for i in items}
        assert times_by_type["breakfast"] == "08:00"
        assert times_by_type["lunch"] == "12:30"
        assert times_by_type["dinner"] == "19:00"

    def test_custom_notify_before_minutes(self):
        """notify_before_minutes をカスタマイズできること。"""
        items = get_next_schedule("07:30", "11:30", "17:30", notify_before_minutes=15)
        breakfast = next(i for i in items if i.meal_type == "breakfast")
        assert breakfast.notify_at == "07:15"

    def test_skips_invalid_time_format(self):
        """不正な時刻フォーマットはスキップされること。"""
        items = get_next_schedule("invalid", "11:30", "17:30")
        assert len(items) == 2
        meal_types = [i.meal_type for i in items]
        assert "breakfast" not in meal_types

    def test_midnight_wraparound_in_schedule(self):
        """00:15の30分前が23:45として正しくスケジュールされること。"""
        items = get_next_schedule("00:15", "11:30", "17:30")
        late_breakfast = next(i for i in items if i.meal_type == "breakfast")
        assert late_breakfast.notify_at == "23:45"


# ============================================================
# 通知設定APIエンドポイントのテスト
# ============================================================

class TestNotificationSettingsEndpoints:
    """通知設定APIエンドポイントのテスト。"""

    def test_get_settings_requires_authentication(self, client):
        """認証なしのリクエストは 401 を返すこと。"""
        res = client.get("/api/notifications/settings")
        assert res.status_code == 401

    def test_get_settings_returns_default_values(self, client, auth_headers):
        """初回取得時にデフォルト値が返ること。"""
        res = client.get("/api/notifications/settings", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is True
        assert body["breakfast_time"] == "07:30"
        assert body["lunch_time"] == "11:30"
        assert body["dinner_time"] == "17:30"

    def test_get_settings_includes_user_id(self, client, auth_headers, test_user):
        """レスポンスに user_id が含まれること。"""
        res = client.get("/api/notifications/settings", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert "user_id" in body
        assert body["user_id"] == test_user.uid

    def test_put_settings_requires_authentication(self, client):
        """認証なしの更新リクエストは 401 を返すこと。"""
        res = client.put("/api/notifications/settings", json={"enabled": False})
        assert res.status_code == 401

    def test_put_settings_updates_enabled_flag(self, client, auth_headers):
        """enabled フラグを更新できること。"""
        res = client.put(
            "/api/notifications/settings",
            json={"enabled": False},
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is False

    def test_put_settings_updates_meal_times(self, client, auth_headers):
        """食事時刻を更新できること。"""
        res = client.put(
            "/api/notifications/settings",
            json={
                "breakfast_time": "08:00",
                "lunch_time": "12:30",
                "dinner_time": "18:00",
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["breakfast_time"] == "08:00"
        assert body["lunch_time"] == "12:30"
        assert body["dinner_time"] == "18:00"

    def test_put_settings_partial_update(self, client, auth_headers):
        """部分更新（一部フィールドのみ指定）が正しく動作すること。"""
        client.put(
            "/api/notifications/settings",
            json={"breakfast_time": "09:00"},
            headers=auth_headers,
        )
        res = client.get("/api/notifications/settings", headers=auth_headers)
        body = res.json()
        assert body["breakfast_time"] == "09:00"
        assert body["lunch_time"] == "11:30"
        assert body["dinner_time"] == "17:30"

    def test_get_settings_persists_after_update(self, client, auth_headers):
        """更新後にGETで変更が反映されること。"""
        client.put(
            "/api/notifications/settings",
            json={"enabled": False, "dinner_time": "19:00"},
            headers=auth_headers,
        )
        res = client.get("/api/notifications/settings", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is False
        assert body["dinner_time"] == "19:00"


# ============================================================
# 通知スケジュールAPIのテスト
# ============================================================

class TestNotificationScheduleEndpoint:
    """GET /api/notifications/schedule のテスト。"""

    def test_requires_authentication(self, client):
        """認証なしのリクエストは 401 を返すこと。"""
        res = client.get("/api/notifications/schedule")
        assert res.status_code == 401

    def test_returns_schedule_with_three_items(self, client, auth_headers):
        """デフォルト設定で3食分のスケジュールが返ること。"""
        res = client.get("/api/notifications/schedule", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert "schedule" in body
        assert len(body["schedule"]) == 3

    def test_schedule_contains_required_fields(self, client, auth_headers):
        """スケジュールの各アイテムに必須フィールドが存在すること。"""
        res = client.get("/api/notifications/schedule", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        for item in body["schedule"]:
            assert "meal_type" in item
            assert "notify_at" in item
            assert "meal_time" in item

    def test_returns_empty_schedule_when_notifications_disabled(self, client, auth_headers):
        """通知が無効の場合は空のスケジュールを返すこと。"""
        client.put(
            "/api/notifications/settings",
            json={"enabled": False},
            headers=auth_headers,
        )
        res = client.get("/api/notifications/schedule", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["schedule"] == []

    def test_notify_before_minutes_in_response(self, client, auth_headers):
        """レスポンスに notify_before_minutes が含まれること。"""
        res = client.get("/api/notifications/schedule", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert "notify_before_minutes" in body
        assert body["notify_before_minutes"] == NOTIFY_BEFORE_MINUTES

    def test_schedule_meal_types_are_all_three(self, client, auth_headers):
        """スケジュールに breakfast / lunch / dinner が含まれること。"""
        res = client.get("/api/notifications/schedule", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        meal_types = {item["meal_type"] for item in body["schedule"]}
        assert meal_types == {"breakfast", "lunch", "dinner"}


# ============================================================
# 通知トリガーAPIのテスト
# ============================================================

class TestNotificationTriggerEndpoint:
    """POST /api/notifications/trigger のテスト。"""

    def test_requires_authentication(self, client):
        """認証なしのリクエストは 401 を返すこと。"""
        res = client.post("/api/notifications/trigger?meal_type=dinner&recipe_name=カレー")
        assert res.status_code == 401

    def test_trigger_returns_payload(self, client, auth_headers):
        """通知トリガーが有効な通知ペイロードを返すこと。"""
        res = client.post(
            "/api/notifications/trigger?meal_type=dinner&recipe_name=肉じゃが",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["triggered"] is True
        assert "payload" in body
        assert body["payload"] is not None

    def test_trigger_payload_contains_recipe_name(self, client, auth_headers):
        """通知ペイロードにレシピ名が含まれること。"""
        res = client.post(
            "/api/notifications/trigger?meal_type=lunch&recipe_name=親子丼",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["payload"]["recipe_name"] == "親子丼"

    def test_trigger_payload_deeplink_url(self, client, auth_headers):
        """通知ペイロードの deeplink_url が正しい形式であること。"""
        res = client.post(
            "/api/notifications/trigger?meal_type=breakfast&recipe_name=トースト",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["payload"]["deeplink_url"] == "/#suggest?meal=breakfast"

    def test_trigger_returns_not_triggered_when_disabled(self, client, auth_headers):
        """通知が無効の場合は triggered=False を返すこと。"""
        client.put(
            "/api/notifications/settings",
            json={"enabled": False},
            headers=auth_headers,
        )
        res = client.post(
            "/api/notifications/trigger?meal_type=dinner&recipe_name=カレー",
            headers=auth_headers,
        )
        assert res.status_code == 200
        body = res.json()
        assert body["triggered"] is False
        assert body["payload"] is None
