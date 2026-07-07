"""
ユーザー別1日リクエスト上限のユニットテスト（Issue #88）。
インメモリストアを使用するため外部依存なし。
"""
import pytest
from app.daily_limit import (
    DAILY_LIMITS,
    check_and_increment,
    get_status,
    reset_for_test,
)


@pytest.fixture(autouse=True)
def clear_store():
    reset_for_test()
    yield
    reset_for_test()


class TestCheckAndIncrement:
    def test_propose_allowed_within_limit(self):
        uid = "user-propose-1"
        for i in range(DAILY_LIMITS["propose"]):
            status = check_and_increment(uid, "propose")
            assert status.allowed
            assert status.current == i + 1

    def test_propose_blocked_at_limit(self):
        uid = "user-propose-2"
        for _ in range(DAILY_LIMITS["propose"]):
            check_and_increment(uid, "propose")

        status = check_and_increment(uid, "propose")
        assert not status.allowed
        assert status.current == DAILY_LIMITS["propose"]
        assert status.reset_at_jst  # リセット時刻が返る

    def test_vision_allowed_within_limit(self):
        uid = "user-vision-1"
        for i in range(DAILY_LIMITS["vision"]):
            status = check_and_increment(uid, "vision")
            assert status.allowed

    def test_vision_blocked_at_limit(self):
        uid = "user-vision-2"
        for _ in range(DAILY_LIMITS["vision"]):
            check_and_increment(uid, "vision")

        status = check_and_increment(uid, "vision")
        assert not status.allowed

    def test_limits_are_per_user(self):
        uid_a = "user-a"
        uid_b = "user-b"
        for _ in range(DAILY_LIMITS["propose"]):
            check_and_increment(uid_a, "propose")

        status_b = check_and_increment(uid_b, "propose")
        assert status_b.allowed

    def test_limits_are_per_action(self):
        uid = "user-cross"
        for _ in range(DAILY_LIMITS["propose"]):
            check_and_increment(uid, "propose")

        status = check_and_increment(uid, "vision")
        assert status.allowed

    def test_voice_seconds_increment_by_delta(self):
        uid = "user-voice-1"
        status = check_and_increment(uid, "voice_seconds", delta=60)
        assert status.allowed
        assert status.current == 60

    def test_voice_seconds_blocked_when_exhausted(self):
        uid = "user-voice-2"
        check_and_increment(uid, "voice_seconds", delta=DAILY_LIMITS["voice_seconds"])
        status = check_and_increment(uid, "voice_seconds", delta=1)
        assert not status.allowed

    def test_reset_at_jst_is_present(self):
        uid = "user-reset"
        status = check_and_increment(uid, "propose")
        assert "JST" in status.reset_at_jst


class TestGetStatus:
    def test_get_status_no_usage(self):
        uid = "user-status-fresh"
        status = get_status(uid, "propose")
        assert status.allowed
        assert status.current == 0
        assert status.limit == DAILY_LIMITS["propose"]

    def test_get_status_does_not_increment(self):
        uid = "user-status-noinc"
        get_status(uid, "propose")
        get_status(uid, "propose")
        status = get_status(uid, "propose")
        assert status.current == 0

    def test_get_status_reflects_increments(self):
        uid = "user-status-inc"
        check_and_increment(uid, "vision")
        check_and_increment(uid, "vision")
        status = get_status(uid, "vision")
        assert status.current == 2
        assert status.allowed


class TestDailyLimitEndpoints:
    def test_vision_returns_429_after_limit(self, client, auth_headers):
        uid = "test-user-001"
        for _ in range(DAILY_LIMITS["vision"]):
            check_and_increment(uid, "vision")

        # 最小限のJPEGバイト列（SOI + EOI）
        minimal_jpg = bytes([0xFF, 0xD8, 0xFF, 0xD9])
        res = client.post(
            "/api/vision",
            headers=auth_headers,
            files={"file": ("test.jpg", minimal_jpg, "image/jpeg")},
        )
        assert res.status_code == 429
        body = res.json()
        assert "detail" in body
        assert body["detail"]["limit"] == DAILY_LIMITS["vision"]
        assert "reset_at" in body["detail"]

    def test_propose_returns_429_after_limit(self, client, auth_headers):
        uid = "test-user-001"
        for _ in range(DAILY_LIMITS["propose"]):
            check_and_increment(uid, "propose")

        res = client.post(
            "/api/propose",
            headers=auth_headers,
            data={"cooking_time": 30, "effort_level": "normal", "mood_tags": "[]", "mood_freetext": ""},
        )
        assert res.status_code == 429
        body = res.json()
        assert body["detail"]["limit"] == DAILY_LIMITS["propose"]
