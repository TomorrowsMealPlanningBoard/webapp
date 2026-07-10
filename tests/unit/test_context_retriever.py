"""
Issue #29: Context Retriever Agent（ハイブリッド検索によるコンテキスト構築）ユニットテスト

- 層1（ハード制約）が決定的フィルタで取得できること
- 層2（構造化FB）の negative_tags / positive_tags が集約されること
- 層3（ベクトル検索）が negative_tags をメタデータフィルタとして除外すること
- async インターフェースであり、Vision Analyzer 等と並列実行できること
- 出力が RetrievedContext として型定義されていること
"""
import asyncio
import uuid

import pytest

from app.agents.context_retriever import (
    ContextRetrieverAgent,
    HardConstraints,
    InMemoryVectorSearchClient,
    RecipeSnippet,
    RetrievedContext,
    StructuredFeedbackContext,
)


# ------------------------------------------------------------------ tests --

def test_retrieve_returns_typed_context(mock_firestore):
    mock_firestore.add_user(uid="ctx-user-001", email="ctx-user-001@example.com",
                             preferences={"allergies": ["卵"], "dislikes": ["ナス"],
                                          "goal": "diet", "kitchen_tools": ["炊飯器"]})
    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="ctx-user-001"))

    assert isinstance(result, RetrievedContext)
    assert isinstance(result.hard_constraints, HardConstraints)
    assert isinstance(result.structured_feedback, StructuredFeedbackContext)
    assert isinstance(result.similar_snippets, list)
    assert result.user_id == "ctx-user-001"


def test_layer1_hard_constraints_are_deterministic(mock_firestore):
    """層1: アレルギー・調理器具・禁止食材が決定的フィルタで取得できること"""
    mock_firestore.add_user(
        uid="ctx-user-002", email="ctx-user-002@example.com",
        preferences={
            "allergies": ["卵", "そば"],
            "dislikes": ["ナス", "セロリ"],
            "goal": "none",
            "kitchen_tools": ["オーブン"],
        },
    )
    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="ctx-user-002"))

    assert set(result.hard_constraints.allergies) == {"卵", "そば"}
    assert set(result.hard_constraints.forbidden_ingredients) == {"ナス", "セロリ"}
    assert set(result.hard_constraints.available_kitchen_tools) == {"オーブン"}


def test_layer1_defaults_when_no_preferences(mock_firestore):
    """層1: preferences が未設定でも例外を出さず空リストを返すこと"""
    mock_firestore.add_user(uid="ctx-user-003", email="ctx-user-003@example.com", preferences=None)
    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="ctx-user-003"))

    assert result.hard_constraints.allergies == []
    assert result.hard_constraints.forbidden_ingredients == []
    assert result.hard_constraints.available_kitchen_tools == []


def test_layer2_aggregates_negative_and_positive_tags(mock_firestore):
    """層2: 複数FBレコードの negative_tags / positive_tags が重複なく集約されること"""
    mock_firestore.add_user(uid="ctx-user-004", email="ctx-user-004@example.com")
    mock_firestore.add_feedback("ctx-user-004", id=str(uuid.uuid4()), recipe_id="r1",
                                  feedback_type="reject", tags=["揚げ物", "辛い"])
    mock_firestore.add_feedback("ctx-user-004", id=str(uuid.uuid4()), recipe_id="r2",
                                  feedback_type="reject", tags=["辛い"])
    mock_firestore.add_feedback("ctx-user-004", id=str(uuid.uuid4()), recipe_id="r3",
                                  feedback_type="cooked", tags=["和食"])
    mock_firestore.add_feedback("ctx-user-004", id=str(uuid.uuid4()), recipe_id="r4",
                                  feedback_type="cooked", tags=["時短", "和食"])

    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="ctx-user-004"))

    assert set(result.structured_feedback.negative_tags) == {"揚げ物", "辛い"}
    assert set(result.structured_feedback.positive_tags) == {"和食", "時短"}


def test_layer2_only_uses_current_user_feedback(mock_firestore):
    """層2: 他ユーザーのFBが混入しないこと"""
    mock_firestore.add_user(uid="user-a", email="user-a@example.com")
    mock_firestore.add_user(uid="user-b", email="user-b@example.com")
    mock_firestore.add_feedback("user-a", id=str(uuid.uuid4()), recipe_id="r1",
                                  feedback_type="reject", tags=["辛い"])
    mock_firestore.add_feedback("user-b", id=str(uuid.uuid4()), recipe_id="r2",
                                  feedback_type="reject", tags=["甘い"])

    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="user-a"))

    assert result.structured_feedback.negative_tags == ["辛い"]


def test_layer3_hybrid_search_excludes_negative_tags(mock_firestore):
    """層3: ベクトル検索結果から negative_tags に該当するレシピが除外されること"""
    mock_firestore.add_user(uid="ctx-user-005", email="ctx-user-005@example.com")
    mock_firestore.add_feedback("ctx-user-005", id=str(uuid.uuid4()), recipe_id="r1",
                                  feedback_type="reject", tags=["揚げ物"])

    corpus = [
        RecipeSnippet(id="1", text="鶏の唐揚げ 揚げ物レシピ", source="external_recipe", tags=["揚げ物"]),
        RecipeSnippet(id="2", text="鶏の照り焼き ヘルシーレシピ", source="external_recipe", tags=["焼き物"]),
    ]
    client = InMemoryVectorSearchClient(corpus=corpus)
    agent = ContextRetrieverAgent(vector_search_client=client)

    result = asyncio.run(agent.retrieve(user_id="ctx-user-005", query_text="鶏肉を使ったレシピ", top_k=5))

    ids = [s.id for s in result.similar_snippets]
    assert "1" not in ids
    assert "2" in ids


def test_layer3_respects_top_k(mock_firestore):
    """層3: top_k で指定した件数以下しか返らないこと"""
    mock_firestore.add_user(uid="ctx-user-006", email="ctx-user-006@example.com")
    corpus = [
        RecipeSnippet(id=str(i), text=f"レシピ{i} 野菜炒め", source="external_recipe")
        for i in range(10)
    ]
    client = InMemoryVectorSearchClient(corpus=corpus)
    agent = ContextRetrieverAgent(vector_search_client=client)

    result = asyncio.run(agent.retrieve(user_id="ctx-user-006", query_text="野菜炒めのレシピ", top_k=3))

    assert len(result.similar_snippets) <= 3


def test_layer3_empty_query_returns_no_snippets(mock_firestore):
    """層3: query_text が空の場合はベクトル検索を実行せず空リストを返すこと"""
    mock_firestore.add_user(uid="ctx-user-007", email="ctx-user-007@example.com")
    corpus = [RecipeSnippet(id="1", text="何かのレシピ", source="external_recipe")]
    client = InMemoryVectorSearchClient(corpus=corpus)
    agent = ContextRetrieverAgent(vector_search_client=client)

    result = asyncio.run(agent.retrieve(user_id="ctx-user-007", query_text="", top_k=3))

    assert result.similar_snippets == []


def test_retrieve_raises_for_unknown_user(mock_firestore):
    """存在しないユーザーIDでは例外を送出すること"""
    agent = ContextRetrieverAgent()
    with pytest.raises(ValueError):
        asyncio.run(agent.retrieve(user_id="does-not-exist"))


def test_retrieve_is_async_and_parallelizable_with_other_coroutines(mock_firestore):
    """Vision Analyzer Agent 等と並列実行可能な async インターフェースであること"""
    mock_firestore.add_user(uid="ctx-user-008", email="ctx-user-008@example.com")
    agent = ContextRetrieverAgent()

    async def fake_vision_analyzer():
        await asyncio.sleep(0)
        return {"ingredients": []}

    async def run_parallel():
        return await asyncio.gather(
            agent.retrieve(user_id="ctx-user-008"),
            fake_vision_analyzer(),
        )

    context_result, vision_result = asyncio.run(run_parallel())

    assert isinstance(context_result, RetrievedContext)
    assert vision_result == {"ingredients": []}


def test_hard_constraints_not_used_as_vector_filter(mock_firestore):
    """層1（アレルギー等）はベクトル検索の exclude_tags には渡らないこと"""
    mock_firestore.add_user(
        uid="ctx-user-009", email="ctx-user-009@example.com",
        preferences={
            "allergies": ["えび"],
            "dislikes": ["ナス"],
            "goal": "none",
            "kitchen_tools": [],
        },
    )

    captured_exclude_tags = []

    class SpyVectorSearchClient:
        async def search(self, user_id, query_text, top_k, exclude_tags=()):
            captured_exclude_tags.extend(exclude_tags)
            return []

    agent = ContextRetrieverAgent(vector_search_client=SpyVectorSearchClient())
    asyncio.run(agent.retrieve(user_id="ctx-user-009", query_text="えびを使ったレシピ", top_k=3))

    assert "えび" not in captured_exclude_tags
    assert "ナス" not in captured_exclude_tags


# ---- 回帰テスト: 層3ベクトル検索(Memory Bank等)失敗時のフォールバック ----
# /api/propose で mood_tags 指定時に確定的 HTTP 500 (output_context KeyError) を
# 起こしていたリグレッションの再発防止。層3は「好み学習＝加点要素」であり、
# ここが落ちても層1/層2の決定的制約と生成は独立に成立させる（SPEC §3）。


def test_vector_search_failure_falls_back_to_empty_snippets(mock_firestore):
    """
    ベクトル検索(vector_search_client.search)が例外を投げても retrieve() は
    例外を伝播させず、similar_snippets を空にして RetrievedContext を返す。
    層1（ハード制約）は決定的に取得され続けること。
    """
    mock_firestore.add_user(
        uid="ctx-user-fb1", email="ctx-user-fb1@example.com",
        preferences={
            "allergies": ["卵"],
            "dislikes": ["ナス"],
            "goal": "none",
            "kitchen_tools": ["炊飯器"],
        },
    )

    class FailingVectorSearchClient:
        async def search(self, user_id, query_text, top_k, exclude_tags=()):
            # 本番の Memory Bank(search_memory) が NotFound/PermissionDenied 等で
            # 例外を送出する状況を模擬する。
            raise RuntimeError("Memory Bank search_memory failed (simulated)")

    agent = ContextRetrieverAgent(vector_search_client=FailingVectorSearchClient())

    # mood_tags 相当の非空 query_text を渡すと従来は search が走り例外→500 だった。
    result = asyncio.run(
        agent.retrieve(user_id="ctx-user-fb1", query_text="肉料理", top_k=3)
    )

    assert isinstance(result, RetrievedContext)
    # 層3は空にフォールバック
    assert result.similar_snippets == []
    # 層1（ハード制約）は影響を受けず決定的に取得され続ける
    assert set(result.hard_constraints.allergies) == {"卵"}
    assert set(result.hard_constraints.forbidden_ingredients) == {"ナス"}
    assert set(result.hard_constraints.available_kitchen_tools) == {"炊飯器"}


def test_empty_query_skips_vector_search_even_if_client_would_fail(mock_firestore):
    """query_text が空なら search を呼ばず即空リスト（従来無害だった経路の維持）。"""
    mock_firestore.add_user(
        uid="ctx-user-fb2", email="ctx-user-fb2@example.com",
    )

    called = {"n": 0}

    class ExplodingVectorSearchClient:
        async def search(self, user_id, query_text, top_k, exclude_tags=()):
            called["n"] += 1
            raise RuntimeError("should not be called")

    agent = ContextRetrieverAgent(vector_search_client=ExplodingVectorSearchClient())
    result = asyncio.run(agent.retrieve(user_id="ctx-user-fb2", query_text=""))

    assert result.similar_snippets == []
    assert called["n"] == 0
