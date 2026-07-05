"""
Issue #29: Context Retriever Agent（ハイブリッド検索によるコンテキスト構築）ユニットテスト

- 層1（ハード制約）が決定的フィルタで取得できること
- 層2（構造化FB）の negative_tags / positive_tags が集約されること
- 層3（ベクトル検索）が negative_tags をメタデータフィルタとして除外すること（ハイブリッド検索）
- async インターフェースであり、Vision Analyzer 等と並列実行できること
- 出力が RetrievedContext として型定義されていること
"""
import asyncio

import pytest

from app.agents.context_retriever import (
    ContextRetrieverAgent,
    HardConstraints,
    InMemoryVectorSearchClient,
    RecipeSnippet,
    RetrievedContext,
    StructuredFeedbackContext,
)
from app.models import Feedback, User
from app.auth import get_password_hash


# ------------------------------------------------------------------ helpers --

_DEFAULT_PREFERENCES = {
    "allergies": ["卵", "えび"],
    "dislikes": ["ナス"],
    "goal": "diet",
    "kitchen_tools": ["炊飯器", "電子レンジ"],
}


def _make_user(db, uid="ctx-user-001", preferences=_DEFAULT_PREFERENCES):
    user = User(
        uid=uid,
        email=f"{uid}@example.com",
        hashed_password=get_password_hash("testpassword"),
        display_name="コンテキストテストユーザー",
        preferences=preferences,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_feedback(db, user_id, negative_tags=None, positive_tags=None, free_text=None, recipe_id=None):
    records = []
    if negative_tags:
        fb = Feedback(
            id=f"fb-{user_id}-neg-{negative_tags}",
            user_id=user_id,
            recipe_id=recipe_id or "recipe-dummy",
            feedback_type="reject",
            tags=negative_tags,
        )
        db.add(fb)
        records.append(fb)
    if positive_tags:
        fb = Feedback(
            id=f"fb-{user_id}-pos-{positive_tags}",
            user_id=user_id,
            recipe_id=recipe_id or "recipe-dummy",
            feedback_type="cooked",
            tags=positive_tags,
            rating=4,
        )
        db.add(fb)
        records.append(fb)
    db.commit()
    return records[0] if len(records) == 1 else records


# ------------------------------------------------------------------ tests --

def test_retrieve_returns_typed_context(db):
    """出力が RetrievedContext（Recipe Generator への入力構造）として型定義されていること"""
    user = _make_user(db)
    agent = ContextRetrieverAgent(db=db)

    result = asyncio.run(agent.retrieve(user_id=user.uid))

    assert isinstance(result, RetrievedContext)
    assert isinstance(result.hard_constraints, HardConstraints)
    assert isinstance(result.structured_feedback, StructuredFeedbackContext)
    assert isinstance(result.similar_snippets, list)
    assert result.user_id == user.uid


def test_layer1_hard_constraints_are_deterministic(db):
    """層1: アレルギー・調理器具・禁止食材が決定的フィルタ（属性コピー）で取得できること"""
    user = _make_user(
        db,
        preferences={
            "allergies": ["卵", "そば"],
            "dislikes": ["ナス", "セロリ"],
            "goal": "none",
            "kitchen_tools": ["オーブン"],
        },
    )
    agent = ContextRetrieverAgent(db=db)

    result = asyncio.run(agent.retrieve(user_id=user.uid))

    assert set(result.hard_constraints.allergies) == {"卵", "そば"}
    assert set(result.hard_constraints.forbidden_ingredients) == {"ナス", "セロリ"}
    assert set(result.hard_constraints.available_kitchen_tools) == {"オーブン"}


def test_layer1_defaults_when_no_preferences(db):
    """層1: preferences が未設定でも例外を出さず空リストを返すこと"""
    user = _make_user(db, preferences=None)
    agent = ContextRetrieverAgent(db=db)

    result = asyncio.run(agent.retrieve(user_id=user.uid))

    assert result.hard_constraints.allergies == []
    assert result.hard_constraints.forbidden_ingredients == []
    assert result.hard_constraints.available_kitchen_tools == []


def test_layer2_aggregates_negative_and_positive_tags(db):
    """層2: 複数FBレコードの negative_tags / positive_tags が重複なく集約されること"""
    user = _make_user(db)
    _make_feedback(db, user.uid, negative_tags=["揚げ物", "辛い"], positive_tags=["和食"])
    _make_feedback(db, user.uid, negative_tags=["辛い"], positive_tags=["時短", "和食"])

    agent = ContextRetrieverAgent(db=db)
    result = asyncio.run(agent.retrieve(user_id=user.uid))

    assert set(result.structured_feedback.negative_tags) == {"揚げ物", "辛い"}
    assert set(result.structured_feedback.positive_tags) == {"和食", "時短"}


def test_layer2_only_uses_current_user_feedback(db):
    """層2: 他ユーザーのFBが混入しないこと"""
    user_a = _make_user(db, uid="user-a")
    user_b = _make_user(db, uid="user-b")
    _make_feedback(db, user_a.uid, negative_tags=["辛い"])
    _make_feedback(db, user_b.uid, negative_tags=["甘い"])

    agent = ContextRetrieverAgent(db=db)
    result = asyncio.run(agent.retrieve(user_id=user_a.uid))

    assert result.structured_feedback.negative_tags == ["辛い"]


def test_layer3_hybrid_search_excludes_negative_tags(db):
    """
    層3: ベクトル検索結果から層2の negative_tags に該当するレシピが除外されること
    （ハイブリッド検索: ベクトル類似度 × メタデータフィルタ）
    """
    user = _make_user(db)
    _make_feedback(db, user.uid, negative_tags=["揚げ物"])

    corpus = [
        RecipeSnippet(id="1", text="鶏の唐揚げ 揚げ物レシピ", source="external_recipe", tags=["揚げ物"]),
        RecipeSnippet(id="2", text="鶏の照り焼き ヘルシーレシピ", source="external_recipe", tags=["焼き物"]),
    ]
    client = InMemoryVectorSearchClient(corpus=corpus)
    agent = ContextRetrieverAgent(db=db, vector_search_client=client)

    result = asyncio.run(agent.retrieve(user_id=user.uid, query_text="鶏肉を使ったレシピ", top_k=5))

    ids = [s.id for s in result.similar_snippets]
    assert "1" not in ids  # 除外タグ「揚げ物」を含むため除外される
    assert "2" in ids


def test_layer3_respects_top_k(db):
    """層3: top_k で指定した件数以下しか返らないこと"""
    user = _make_user(db)
    corpus = [
        RecipeSnippet(id=str(i), text=f"レシピ{i} 野菜炒め", source="external_recipe")
        for i in range(10)
    ]
    client = InMemoryVectorSearchClient(corpus=corpus)
    agent = ContextRetrieverAgent(db=db, vector_search_client=client)

    result = asyncio.run(agent.retrieve(user_id=user.uid, query_text="野菜炒めのレシピ", top_k=3))

    assert len(result.similar_snippets) <= 3


def test_layer3_empty_query_returns_no_snippets(db):
    """層3: query_text が空の場合はベクトル検索を実行せず空リストを返すこと"""
    user = _make_user(db)
    corpus = [RecipeSnippet(id="1", text="何かのレシピ", source="external_recipe")]
    client = InMemoryVectorSearchClient(corpus=corpus)
    agent = ContextRetrieverAgent(db=db, vector_search_client=client)

    result = asyncio.run(agent.retrieve(user_id=user.uid, query_text="", top_k=3))

    assert result.similar_snippets == []


def test_retrieve_raises_for_unknown_user(db):
    """存在しないユーザーIDでは例外を送出すること"""
    agent = ContextRetrieverAgent(db=db)
    with pytest.raises(ValueError):
        asyncio.run(agent.retrieve(user_id="does-not-exist"))


def test_retrieve_is_async_and_parallelizable_with_other_coroutines(db):
    """
    Vision Analyzer Agent 等と並列実行可能な async インターフェースであること。
    asyncio.gather で他のコルーチンと同時に実行できることを確認する。
    """
    user = _make_user(db)
    agent = ContextRetrieverAgent(db=db)

    async def fake_vision_analyzer():
        await asyncio.sleep(0)
        return {"ingredients": []}

    async def run_parallel():
        return await asyncio.gather(
            agent.retrieve(user_id=user.uid),
            fake_vision_analyzer(),
        )

    context_result, vision_result = asyncio.run(run_parallel())

    assert isinstance(context_result, RetrievedContext)
    assert vision_result == {"ingredients": []}


def test_hard_constraints_not_used_as_vector_filter(db):
    """
    層1（アレルギー等）はベクトル検索の exclude_tags には渡らないこと。
    層1と層2/3の処理経路が分離されている（決定的フィルタと確率的処理の混在を防ぐ）ことを確認する。
    """
    user = _make_user(
        db,
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

    agent = ContextRetrieverAgent(db=db, vector_search_client=SpyVectorSearchClient())
    asyncio.run(agent.retrieve(user_id=user.uid, query_text="えびを使ったレシピ", top_k=3))

    # ベクトル検索に渡る exclude_tags は層2(negative_tags)由来のみ。層1のアレルギー/禁止食材は含まれない。
    assert "えび" not in captured_exclude_tags
    assert "ナス" not in captured_exclude_tags
