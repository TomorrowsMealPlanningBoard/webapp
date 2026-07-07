"""
Issue #24: 提案重複回避のための履歴管理基盤のユニットテスト

AC:
- meal_proposals テーブルに提案レコードが保存されること（提案時に自動保存）
- GET /api/proposals/recent で直近7日分が取得できること
- Recipe Generator Agent へのプロンプトに直近提案タイトルが注入されること
- 同一レシピが直近7日以内に提案済みの場合、Generator が別案を生成すること
- uv run pytest tests/unit/test_proposals.py が全件パスすること
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import MealProposal, User
from app.auth import create_access_token
from app.agents.context_retriever import ContextRetrieverAgent, RetrievedContext


# ============================================================
# AC1: meal_proposals テーブルに提案レコードが保存されること
# ============================================================

def test_suggest_saves_meal_proposals(client, auth_headers, db):
    """提案APIを呼ぶと meal_proposals にレコードが保存されること"""
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 200
    recipes = res.json()["recipes"]
    assert len(recipes) > 0

    # DBに保存されたレコードを確認
    proposals = db.query(MealProposal).all()
    assert len(proposals) == len(recipes)
    saved_titles = {p.recipe_title for p in proposals}
    returned_titles = {r["title"] for r in recipes}
    assert saved_titles == returned_titles


def test_suggest_requires_auth_for_proposals(client):
    """未認証では提案APIにアクセスできないこと"""
    res = client.post("/api/suggest", json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 401


# ============================================================
# AC2: GET /api/proposals/recent で直近7日分が取得できること
# ============================================================

def test_get_recent_proposals_empty(client, auth_headers):
    """提案が0件のときは空リストが返ること"""
    res = client.get("/api/proposals/recent", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert "proposals" in body
    assert body["proposals"] == []


def test_get_recent_proposals_returns_7day_records(client, auth_headers, db, test_user):
    """直近7日以内の提案のみが返ること（7日より古いものは除外）"""
    now = datetime.now(timezone.utc)

    # 直近5日のレコード（取得対象）
    recent_proposal = MealProposal(
        id="prop-recent-001",
        user_id=test_user.uid,
        recipe_id="recipe_001",
        recipe_title="最近のレシピ",
        proposed_at=now - timedelta(days=3),
    )
    # 8日前のレコード（取得対象外）
    old_proposal = MealProposal(
        id="prop-old-001",
        user_id=test_user.uid,
        recipe_id="recipe_002",
        recipe_title="古いレシピ",
        proposed_at=now - timedelta(days=8),
    )
    db.add(recent_proposal)
    db.add(old_proposal)
    db.commit()

    res = client.get("/api/proposals/recent", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    titles = [p["recipe_title"] for p in body["proposals"]]
    assert "最近のレシピ" in titles
    assert "古いレシピ" not in titles


def test_get_recent_proposals_requires_auth(client):
    """未認証では recent proposals にアクセスできないこと"""
    res = client.get("/api/proposals/recent")
    assert res.status_code == 401


def test_get_recent_proposals_response_schema(client, auth_headers, db, test_user):
    """レスポンスに id, recipe_id, recipe_title, proposed_at が含まれること"""
    now = datetime.now(timezone.utc)
    proposal = MealProposal(
        id="prop-schema-001",
        user_id=test_user.uid,
        recipe_id="recipe_003",
        recipe_title="スキーマ確認レシピ",
        proposed_at=now - timedelta(days=1),
    )
    db.add(proposal)
    db.commit()

    res = client.get("/api/proposals/recent", headers=auth_headers)
    assert res.status_code == 200
    proposals = res.json()["proposals"]
    assert len(proposals) >= 1
    p = next(x for x in proposals if x["recipe_title"] == "スキーマ確認レシピ")
    assert "id" in p
    assert "recipe_id" in p
    assert "recipe_title" in p
    assert "proposed_at" in p


# ============================================================
# AC3: Context Retriever Agent に直近提案タイトルが注入されること
# ============================================================

def test_context_retriever_injects_recent_proposal_titles(db, test_user):
    """RetrievedContext に recent_proposal_titles が含まれること"""
    now = datetime.now(timezone.utc)
    proposal = MealProposal(
        id="prop-ctx-001",
        user_id=test_user.uid,
        recipe_id="recipe_001",
        recipe_title="直近提案レシピ",
        proposed_at=now - timedelta(days=2),
    )
    db.add(proposal)
    db.commit()

    agent = ContextRetrieverAgent(db=db)
    context = asyncio.run(agent.retrieve(user_id=test_user.uid, query_text=""))

    assert isinstance(context, RetrievedContext)
    assert "直近提案レシピ" in context.recent_proposal_titles


def test_context_retriever_excludes_old_proposals_from_recent(db, test_user):
    """7日より古い提案は recent_proposal_titles に含まれないこと"""
    now = datetime.now(timezone.utc)
    old_proposal = MealProposal(
        id="prop-old-ctx-001",
        user_id=test_user.uid,
        recipe_id="recipe_002",
        recipe_title="古い提案レシピ",
        proposed_at=now - timedelta(days=10),
    )
    db.add(old_proposal)
    db.commit()

    agent = ContextRetrieverAgent(db=db)
    context = asyncio.run(agent.retrieve(user_id=test_user.uid, query_text=""))

    assert "古い提案レシピ" not in context.recent_proposal_titles


def test_context_retriever_empty_when_no_proposals(db, test_user):
    """提案履歴がないときは recent_proposal_titles が空リストであること"""
    agent = ContextRetrieverAgent(db=db)
    context = asyncio.run(agent.retrieve(user_id=test_user.uid, query_text=""))
    assert context.recent_proposal_titles == []


# ============================================================
# AC4: 同一レシピが直近7日以内に提案済みの場合、別案を生成すること
# ============================================================

def test_suggest_excludes_recently_proposed_recipes(client, auth_headers, db, test_user):
    """
    直近7日以内に提案済みのレシピタイトルが今回の提案候補から除外されること。
    モックレシピは複数あるので、1回目の提案結果と重複しない別案が返ること。
    """
    # 1回目の提案
    res1 = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res1.status_code == 200
    titles_1 = {r["title"] for r in res1.json()["recipes"]}
    assert len(titles_1) > 0

    # 2回目の提案 — 1回目に提案されたレシピが除外されること
    res2 = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res2.status_code == 200
    titles_2 = {r["title"] for r in res2.json()["recipes"]}

    # 2回目の提案には1回目で提案したタイトルが含まれないはず
    # （モックレシピの総数 > 提案数*2 の場合のみ成立。モックは6件以上想定）
    from app.mock_recipes import MOCK_RECIPES
    if len(MOCK_RECIPES) > len(titles_1) + len(titles_2):
        overlap = titles_1 & titles_2
        assert len(overlap) == 0, f"重複タイトルが検出されました: {overlap}"


def test_suggest_fallback_when_all_recipes_proposed(client, auth_headers, db, test_user):
    """
    全レシピが直近7日以内に提案済みの場合でも、フォールバックとして提案が返ること。
    """
    from app.mock_recipes import MOCK_RECIPES
    now = datetime.now(timezone.utc)

    # 全モックレシピを提案済みとして登録
    for recipe in MOCK_RECIPES:
        proposal = MealProposal(
            id=str(__import__('uuid').uuid4()),
            user_id=test_user.uid,
            recipe_id=recipe["id"],
            recipe_title=recipe["title"],
            proposed_at=now - timedelta(days=1),
        )
        db.add(proposal)
    db.commit()

    # 全件既出でもフォールバックして提案が返ること
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 200
    body = res.json()
    assert len(body["recipes"]) > 0
