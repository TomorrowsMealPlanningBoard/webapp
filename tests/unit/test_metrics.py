"""
Issue #37: アウトカム・ダッシュボード（GET /api/metrics）のユニットテスト

提案履歴管理(#24)・フィードバック機能(#23)・LLM-as-judge eval(#34) は
本チケット時点では未実装のため、以下2つの観点で検証する：
  1. データが全く無い状態でもエラーにならず has_data=False を返すこと（誠実な空表示）
  2. データが存在する場合は算出ロジックが正しく機能すること（将来のデータ蓄積を想定）
"""
from datetime import datetime, timedelta, date

from app.models import Feedback, MealHistory, QualityScoreLog


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


def test_food_waste_reduction_rate_with_data(client, auth_headers, test_user, db):
    """ingredients_used が記録されている場合、使い切り率が算出されること"""
    history = MealHistory(
        id="mh-1",
        user_id=test_user.uid,
        date=date.today(),
        meal_type="dinner",
        status="completed",
        recipe={"title": "テストレシピ"},
        ingredients_used=[
            {"name": "にんじん", "used_quantity": 1, "unit": "本", "was_expiring": True},
            {"name": "牛乳", "used_quantity": 200, "unit": "ml", "was_expiring": False},
        ],
    )
    db.add(history)
    db.commit()

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["food_waste_reduction_rate"]
    assert metric["has_data"] is True
    assert metric["sample_size"] == 2
    assert metric["value"] == 50.0  # 2件中1件が期限切れ間近食材の使用


def test_nutrition_goal_achievement_rate_with_data(client, auth_headers, test_user, db):
    fb1 = Feedback(id="fb-1", user_id=test_user.uid, nutrition_goal_met=True)
    fb2 = Feedback(id="fb-2", user_id=test_user.uid, nutrition_goal_met=False)
    fb3 = Feedback(id="fb-3", user_id=test_user.uid, nutrition_goal_met=None)  # 未記録は集計対象外
    db.add_all([fb1, fb2, fb3])
    db.commit()

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["nutrition_goal_achievement_rate"]
    assert metric["has_data"] is True
    assert metric["sample_size"] == 2
    assert metric["value"] == 50.0


def test_decision_time_with_data(client, auth_headers, test_user, db):
    suggested = datetime.utcnow()
    decided = suggested + timedelta(seconds=120)
    history = MealHistory(
        id="mh-2",
        user_id=test_user.uid,
        date=date.today(),
        meal_type="dinner",
        status="completed",
        recipe={"title": "テストレシピ"},
        suggested_at=suggested,
        decided_at=decided,
    )
    db.add(history)
    db.commit()

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["decision_time"]
    assert metric["has_data"] is True
    assert metric["sample_size"] == 1
    assert metric["value"] == 120.0
    assert metric["unit"] == "seconds"


def test_cooking_time_with_data(client, auth_headers, test_user, db):
    started = datetime.utcnow()
    completed = started + timedelta(minutes=20)
    history = MealHistory(
        id="mh-3",
        user_id=test_user.uid,
        date=date.today(),
        meal_type="dinner",
        status="completed",
        recipe={"title": "テストレシピ"},
        cooking_started_at=started,
        cooking_completed_at=completed,
    )
    db.add(history)
    db.commit()

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["cooking_time"]
    assert metric["has_data"] is True
    assert metric["value"] == 1200.0


def test_quality_score_trend_with_data(client, auth_headers, test_user, db):
    log1 = QualityScoreLog(
        id="qs-1",
        user_id=test_user.uid,
        subject_type="suggestion",
        score=0.8,
        eval_version="v1",
    )
    log2 = QualityScoreLog(
        id="qs-2",
        user_id=test_user.uid,
        subject_type="suggestion",
        score=0.9,
        eval_version="v1",
    )
    db.add_all([log1, log2])
    db.commit()

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    trend = res.json()["quality_score_trend"]
    assert trend["has_data"] is True
    assert trend["sample_size"] == 2
    assert len(trend["points"]) == 2
    assert trend["average"] == 0.85


def test_metrics_isolated_per_user(client, auth_headers, test_user, db):
    """他ユーザーのデータが混入しないこと"""
    other_history = MealHistory(
        id="mh-other",
        user_id="someone-else",
        date=date.today(),
        meal_type="dinner",
        status="completed",
        recipe={"title": "他人のレシピ"},
        ingredients_used=[
            {"name": "豚肉", "used_quantity": 1, "unit": "パック", "was_expiring": True},
        ],
    )
    db.add(other_history)
    db.commit()

    res = client.get("/api/metrics", headers=auth_headers)
    assert res.status_code == 200
    metric = res.json()["food_waste_reduction_rate"]
    assert metric["has_data"] is False
    assert metric["sample_size"] == 0
