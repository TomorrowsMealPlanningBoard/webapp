"""
Issue #37: アウトカム・ダッシュボード（GET /api/metrics）のユニットテスト
"""
from datetime import datetime, timedelta, date, timezone


def test_metrics_requires_auth(client):
    res = client.get("/api/metrics")
    assert res.status_code == 401


def test_metrics_returns_no_data_when_empty(client, auth_headers):
    """データが何も無い状態でも200が返り、各指標がhas_data=Falseであること"""
    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()

    for key in [
        "food_waste_reduction_rate",
        "nutrition_goal_achievement_rate",
        "decision_time",
        "cooking_time",
    ]:
        assert body[key]["has_data"] is False
        assert body[key]["value"] is None
        assert body[key]["sample_size"] == 0

    trend = body["quality_score_trend"]
    assert trend["has_data"] is False
    assert trend["points"] == []
    assert trend["sample_size"] == 0


def test_food_waste_reduction_rate_with_data(client, auth_headers, test_user, mock_firestore):
    """ingredients_used が記録されている場合、使い切り率が算出されること"""
    mock_firestore.add_meal_history(
        user_id=test_user.uid,
        id="mh-1",
        ingredients_used=[
            {"name": "にんじん", "used_quantity": 1, "unit": "本", "was_expiring": True},
            {"name": "牛乳", "used_quantity": 200, "unit": "ml", "was_expiring": False},
        ],
    )

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["food_waste_reduction_rate"]
    assert metric["has_data"] is True
    assert metric["sample_size"] == 2
    assert metric["value"] == 50.0


def test_nutrition_goal_achievement_rate_with_data(client, auth_headers, test_user, mock_firestore):
    mock_firestore.add_feedback(test_user.uid, id="fb-1", recipe_id="r-1",
                                  feedback_type="cooked", nutrition_goal_met=True)
    mock_firestore.add_feedback(test_user.uid, id="fb-2", recipe_id="r-2",
                                  feedback_type="cooked", nutrition_goal_met=False)
    mock_firestore.add_feedback(test_user.uid, id="fb-3", recipe_id="r-3",
                                  feedback_type="cooked", nutrition_goal_met=None)

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["nutrition_goal_achievement_rate"]
    assert metric["has_data"] is True
    assert metric["sample_size"] == 2
    assert metric["value"] == 50.0


def test_decision_time_with_data(client, auth_headers, test_user, mock_firestore):
    suggested = datetime.now(timezone.utc)
    decided = suggested + timedelta(seconds=120)
    mock_firestore.add_meal_history(
        user_id=test_user.uid,
        id="mh-2",
        suggested_at=suggested,
        decided_at=decided,
    )

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["decision_time"]
    assert metric["has_data"] is True
    assert metric["sample_size"] == 1
    assert metric["value"] == 120.0
    assert metric["unit"] == "seconds"


def test_cooking_time_with_data(client, auth_headers, test_user, mock_firestore):
    started = datetime.now(timezone.utc)
    completed = started + timedelta(minutes=20)
    mock_firestore.add_meal_history(
        user_id=test_user.uid,
        id="mh-3",
        cooking_started_at=started,
        cooking_completed_at=completed,
    )

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["cooking_time"]
    assert metric["has_data"] is True
    assert metric["value"] == 1200.0


def test_quality_score_trend_with_data(client, auth_headers, test_user, mock_firestore):
    mock_firestore.add_quality_score_log(id="qs-1", score=0.8, user_id=test_user.uid,
                                          subject_type="suggestion", eval_version="v1")
    mock_firestore.add_quality_score_log(id="qs-2", score=0.9, user_id=test_user.uid,
                                          subject_type="suggestion", eval_version="v1")

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    trend = res.json()["quality_score_trend"]
    assert trend["has_data"] is True
    assert trend["sample_size"] == 2
    assert len(trend["points"]) == 2
    assert trend["average"] == 0.85


def test_metrics_isolated_per_user(client, auth_headers, test_user, mock_firestore):
    """他ユーザーのデータが混入しないこと"""
    mock_firestore.add_meal_history(
        user_id="someone-else",
        id="mh-other",
        ingredients_used=[
            {"name": "豚肉", "used_quantity": 1, "unit": "パック", "was_expiring": True},
        ],
    )

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["food_waste_reduction_rate"]
    assert metric["has_data"] is False
    assert metric["sample_size"] == 0
