"""
Issue #77: Memory Bank による好み学習ループ(ループA)の実装

- `USE_MEMORY_BANK` 未設定時は既存の InMemoryVectorSearchClient を維持すること（共存構成）
- `USE_MEMORY_BANK=true` 時は MemoryBankVectorSearchClient が選択されること
- MemoryBankVectorSearchClient.search が VertexAiMemoryBankService.search_memory の結果を
  RecipeSnippet に変換し、negative_tags を保守的にフィルタすること
- generate_memories が空文字列/空リストの場合は何もしないこと（無駄なAPI呼び出しを避ける）
- POST /api/feedback の cooked FB に comment がある場合、Memory Bank投入がバックグラウンドタスクに
  登録されること（層1のアレルギー等は一切渡さない設計であることをあわせて確認する）
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.context_retriever import InMemoryVectorSearchClient
from app.agents.memory_bank_client import (
    MemoryBankVectorSearchClient,
    build_vector_search_client,
)


def test_build_vector_search_client_defaults_to_in_memory(monkeypatch):
    monkeypatch.delenv("USE_MEMORY_BANK", raising=False)
    client = build_vector_search_client()
    assert isinstance(client, InMemoryVectorSearchClient)


def test_build_vector_search_client_switches_to_memory_bank(monkeypatch):
    monkeypatch.setenv("USE_MEMORY_BANK", "true")
    client = build_vector_search_client()
    assert isinstance(client, MemoryBankVectorSearchClient)


@pytest.mark.asyncio
async def test_memory_bank_search_converts_memories_to_snippets():
    from google.genai import types

    mock_service = MagicMock()
    mock_response = MagicMock()
    mock_response.memories = [
        MagicMock(content=types.Content(parts=[types.Part(text="醤油とみりんの甘辛い味付けが好き")])),
        MagicMock(content=types.Content(parts=[types.Part(text="辛い料理は苦手")])),
    ]
    mock_service.search_memory = AsyncMock(return_value=mock_response)

    client = MemoryBankVectorSearchClient()
    client._service = mock_service

    results = await client.search(user_id="u1", query_text="今日の献立", top_k=5)

    assert len(results) == 2
    assert results[0].source == "memory_bank"
    assert "甘辛い" in results[0].text


@pytest.mark.asyncio
async def test_memory_bank_search_filters_exclude_tags_by_substring():
    """negative_tags（層2）をテキスト部分一致で保守的にフィルタすること"""
    from google.genai import types

    mock_service = MagicMock()
    mock_response = MagicMock()
    mock_response.memories = [
        MagicMock(content=types.Content(parts=[types.Part(text="辛い料理が好き")])),
        MagicMock(content=types.Content(parts=[types.Part(text="甘い味付けが好き")])),
    ]
    mock_service.search_memory = AsyncMock(return_value=mock_response)

    client = MemoryBankVectorSearchClient()
    client._service = mock_service

    results = await client.search(user_id="u1", query_text="献立", top_k=5, exclude_tags=["辛い"])

    assert len(results) == 1
    assert "甘い" in results[0].text


@pytest.mark.asyncio
async def test_memory_bank_search_returns_empty_for_blank_query():
    client = MemoryBankVectorSearchClient()
    results = await client.search(user_id="u1", query_text="", top_k=5)
    assert results == []


@pytest.mark.asyncio
async def test_generate_memories_calls_add_memory_with_texts():
    mock_service = MagicMock()
    mock_service.add_memory = AsyncMock(return_value=None)

    client = MemoryBankVectorSearchClient()
    client._service = mock_service

    await client.generate_memories(user_id="u1", texts=["もう少し塩気が欲しかった"])

    mock_service.add_memory.assert_awaited_once()
    _, kwargs = mock_service.add_memory.call_args
    assert kwargs["user_id"] == "u1"
    assert len(kwargs["memories"]) == 1


@pytest.mark.asyncio
async def test_generate_memories_skips_blank_texts():
    mock_service = MagicMock()
    mock_service.add_memory = AsyncMock(return_value=None)

    client = MemoryBankVectorSearchClient()
    client._service = mock_service

    await client.generate_memories(user_id="u1", texts=["  ", ""])

    mock_service.add_memory.assert_not_awaited()


def test_feedback_cooked_with_comment_triggers_memory_bank_background_task(
    client, auth_headers, monkeypatch
):
    """cooked FBにcommentがある場合、Memory Bank投入がバックグラウンドタスクとして登録されること"""
    captured = {}

    async def fake_generate(user_id, comment):
        captured["user_id"] = user_id
        captured["comment"] = comment

    monkeypatch.setattr("app.main._generate_memories_for_feedback", fake_generate)

    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": "recipe_002",
        "feedback_type": "cooked",
        "tags": ["味付けが最高"],
        "rating": 5,
        "comment": "もう少し塩気が欲しかった",
    })
    assert res.status_code == 200
    assert captured["comment"] == "もう少し塩気が欲しかった"


def test_feedback_reject_does_not_trigger_memory_bank(client, auth_headers, monkeypatch):
    """reject FBはMemory Bank投入の対象外であること（層3の自由記述FBのみが対象）"""
    called = {"count": 0}

    async def fake_generate(user_id, comment):
        called["count"] += 1

    monkeypatch.setattr("app.main._generate_memories_for_feedback", fake_generate)

    res = client.post("/api/feedback", headers=auth_headers, json={
        "recipe_id": "recipe_003",
        "feedback_type": "reject",
        "tags": ["辛い"],
    })
    assert res.status_code == 200
    assert called["count"] == 0
