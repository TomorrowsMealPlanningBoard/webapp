"""
Issue #24: 提案重複回避のための履歴管理基盤のユニットテスト（Firestore 実装）
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.auth import create_access_token
from app.agents.context_retriever import ContextRetrieverAgent, RetrievedContext


# ============================================================
# AC1: 提案APIを呼ぶと meal_proposals にレコードが保存されること
# ============================================================

def test_suggest_saves_meal_proposals(client, auth_headers, mock_firestore, test_user):
    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 200
    recipes = res.json()["recipes"]
    assert len(recipes) > 0

    proposals = mock_firestore.meal_proposals.get(test_user.uid, [])
    assert len(proposals) == len(recipes)
    saved_titles = {p["recipe_title"] for p in proposals}
    returned_titles = {r["title"] for r in recipes}
    assert saved_titles == returned_titles


def test_suggest_requires_auth_for_proposals(client):
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
    res = client.get("/api/proposals/recent", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert "proposals" in body
    assert body["proposals"] == []


def test_get_recent_proposals_returns_7day_records(client, auth_headers, test_user, mock_firestore):
    now = datetime.now(timezone.utc)
    mock_firestore.add_meal_proposal(
        user_id=test_user.uid,
        id="prop-recent-001",
        recipe_id="recipe_001",
        recipe_title="最近のレシピ",
        proposed_at=now - timedelta(days=3),
    )
    mock_firestore.add_meal_proposal(
        user_id=test_user.uid,
        id="prop-old-001",
        recipe_id="recipe_002",
        recipe_title="古いレシピ",
        proposed_at=now - timedelta(days=8),
    )

    res = client.get("/api/proposals/recent", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    titles = [p["recipe_title"] for p in body["proposals"]]
    assert "最近のレシピ" in titles
    assert "古いレシピ" not in titles


def test_get_recent_proposals_requires_auth(client):
    res = client.get("/api/proposals/recent")
    assert res.status_code == 401


def test_get_recent_proposals_response_schema(client, auth_headers, test_user, mock_firestore):
    now = datetime.now(timezone.utc)
    mock_firestore.add_meal_proposal(
        user_id=test_user.uid,
        id="prop-schema-001",
        recipe_id="recipe_003",
        recipe_title="スキーマ確認レシピ",
        proposed_at=now - timedelta(days=1),
    )

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

def test_context_retriever_injects_recent_proposal_titles(mock_firestore, test_user):
    now = datetime.now(timezone.utc)
    mock_firestore.add_meal_proposal(
        user_id=test_user.uid,
        id="prop-ctx-001",
        recipe_id="recipe_001",
        recipe_title="直近提案レシピ",
        proposed_at=now - timedelta(days=2),
    )

    agent = ContextRetrieverAgent()
    context = asyncio.run(agent.retrieve(user_id=test_user.uid, query_text=""))

    assert isinstance(context, RetrievedContext)
    assert "直近提案レシピ" in context.recent_proposal_titles


def test_context_retriever_excludes_old_proposals_from_recent(mock_firestore, test_user):
    now = datetime.now(timezone.utc)
    mock_firestore.add_meal_proposal(
        user_id=test_user.uid,
        id="prop-old-ctx-001",
        recipe_id="recipe_002",
        recipe_title="古い提案レシピ",
        proposed_at=now - timedelta(days=10),
    )

    agent = ContextRetrieverAgent()
    context = asyncio.run(agent.retrieve(user_id=test_user.uid, query_text=""))

    assert "古い提案レシピ" not in context.recent_proposal_titles


def test_context_retriever_empty_when_no_proposals(mock_firestore, test_user):
    agent = ContextRetrieverAgent()
    context = asyncio.run(agent.retrieve(user_id=test_user.uid, query_text=""))
    assert context.recent_proposal_titles == []


# ============================================================
# AC4: 同一レシピが直近7日以内に提案済みの場合、別案を生成すること
# ============================================================

def test_suggest_excludes_recently_proposed_recipes(client, auth_headers, test_user, mock_firestore):
    res1 = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res1.status_code == 200
    titles_1 = {r["title"] for r in res1.json()["recipes"]}
    assert len(titles_1) > 0

    res2 = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res2.status_code == 200
    titles_2 = {r["title"] for r in res2.json()["recipes"]}

    from app.mock_recipes import MOCK_RECIPES
    if len(MOCK_RECIPES) > len(titles_1) + len(titles_2):
        overlap = titles_1 & titles_2
        assert len(overlap) == 0, f"重複タイトルが検出されました: {overlap}"


def test_suggest_fallback_when_all_recipes_proposed(client, auth_headers, test_user, mock_firestore):
    from app.mock_recipes import MOCK_RECIPES
    now = datetime.now(timezone.utc)

    for recipe in MOCK_RECIPES:
        mock_firestore.add_meal_proposal(
            user_id=test_user.uid,
            id=f"fallback-{recipe['id']}",
            recipe_id=recipe["id"],
            recipe_title=recipe["title"],
            proposed_at=now - timedelta(days=1),
        )

    res = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 999,
        "effort_level": "normal",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 200
    body = res.json()
    assert len(body["recipes"]) > 0
