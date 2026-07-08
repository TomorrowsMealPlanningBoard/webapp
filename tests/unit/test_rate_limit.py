"""
LLM課金暴走防止のためのレート制限テスト（Issue #56）。
"""
import pytest
from app.main import limiter
from app.auth import create_access_token


@pytest.fixture
def enabled_limiter():
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.enabled = False
    limiter.reset()


def test_suggest_rate_limit_returns_429_after_exceeding(client, auth_headers, enabled_limiter):
    """/api/suggest は1分間に5回を超えると429を返すこと"""
    for _ in range(5):
        res = client.post("/api/suggest", headers=auth_headers, json={
            "cooking_time": 30,
            "effort_level": "easy",
            "mood_tags": [],
            "mood_freetext": "",
        })
        assert res.status_code == 200

    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 429
    assert "detail" in res.json()


def test_propose_rate_limit_returns_429_after_exceeding(client, auth_headers, enabled_limiter):
    """/api/propose は1分間に3回を超えると429を返すこと"""
    for _ in range(3):
        res = client.post(
            "/api/propose",
            headers=auth_headers,
            data={
                "cooking_time": 30,
                "effort_level": "normal",
                "mood_tags": "[]",
                "mood_freetext": "",
            },
        )
        assert res.status_code in (200, 500)

    res = client.post(
        "/api/propose",
        headers=auth_headers,
        data={
            "cooking_time": 30,
            "effort_level": "normal",
            "mood_tags": "[]",
            "mood_freetext": "",
        },
    )
    assert res.status_code == 429


def test_rate_limit_is_per_user(client, auth_headers, enabled_limiter, mock_firestore):
    """レート制限はユーザー単位であり、別ユーザーは影響を受けないこと"""
    other_user = mock_firestore.add_user(
        uid="test-user-002",
        email="other@example.com",
        display_name="別のテストユーザー",
    )
    other_token = create_access_token(data={"sub": other_user.uid})
    other_headers = {"Authorization": f"Bearer {other_token}"}

    for _ in range(5):
        res = client.post("/api/suggest", headers=auth_headers, json={
            "cooking_time": 30,
            "effort_level": "easy",
            "mood_tags": [],
            "mood_freetext": "",
        })
        assert res.status_code == 200

    res = client.post("/api/suggest", headers=other_headers, json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 200
