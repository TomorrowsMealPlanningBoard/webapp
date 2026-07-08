"""
Issue #21: 過去のフィードバックを次回の提案プロンプトへ反映する処理のユニットテスト

AC対応:
1. Context Retriever Agent が negative_tags（ハードフィルタ）、positive_tags（参考情報）、
   free_text（ベクトル検索上位3件）をプロンプトに注入すること
2. 不採用タグに該当するレシピ属性が提案に含まれないこと（決定的フィルタ）
"""
import asyncio
import uuid

import pytest

from app.agents.context_retriever import (
    ContextRetrieverAgent,
    InMemoryVectorSearchClient,
    RecipeSnippet,
)


# ============================================================
# AC-1: negative_tags / positive_tags / free_text がコンテキストに注入されること
# ============================================================

class TestFeedbackInjectedToContext:
    def test_negative_tags_injected_to_context(self, mock_firestore):
        """不採用FBのタグが negative_tags として RetrievedContext に含まれること"""
        mock_firestore.add_user(uid="fb-loop-user", email="fb-loop-user@example.com")
        mock_firestore.add_feedback("fb-loop-user", id=str(uuid.uuid4()), recipe_id="r1",
                                      feedback_type="reject", tags=["#揚げ物", "#辛い"])

        agent = ContextRetrieverAgent()
        ctx = asyncio.run(agent.retrieve(user_id="fb-loop-user"))

        assert "#揚げ物" in ctx.structured_feedback.negative_tags
        assert "#辛い" in ctx.structured_feedback.negative_tags

    def test_positive_tags_injected_to_context(self, mock_firestore):
        """調理後FBのタグが positive_tags として RetrievedContext に含まれること"""
        mock_firestore.add_user(uid="fb-loop-user-2", email="fb-loop-user-2@example.com")
        mock_firestore.add_feedback("fb-loop-user-2", id=str(uuid.uuid4()), recipe_id="r1",
                                      feedback_type="cooked", tags=["味付けが最高", "手軽だった"], rating=5)

        agent = ContextRetrieverAgent()
        ctx = asyncio.run(agent.retrieve(user_id="fb-loop-user-2"))

        assert "味付けが最高" in ctx.structured_feedback.positive_tags
        assert "手軽だった" in ctx.structured_feedback.positive_tags

    def test_free_text_comment_seeded_to_vector_corpus(self, mock_firestore):
        """自由記述FB（comment）がベクトル検索コーパスにシードされること"""
        mock_firestore.add_user(uid="fb-loop-user-3", email="fb-loop-user-3@example.com")
        mock_firestore.add_feedback("fb-loop-user-3", id="fb-with-comment", recipe_id="r1",
                                      feedback_type="cooked", tags=["手軽だった"], rating=5,
                                      comment="もう少し塩気が欲しかった。薄味の料理だった")

        agent = ContextRetrieverAgent()
        ctx = asyncio.run(agent.retrieve(
            user_id="fb-loop-user-3",
            query_text="塩気のある料理を作りたい",
            top_k=3,
        ))

        assert len(ctx.similar_snippets) >= 1
        ids = [s.id for s in ctx.similar_snippets]
        assert any("fb_comment_" in sid for sid in ids)

    def test_free_text_limited_to_top_k(self, mock_firestore):
        """自由記述FBが複数あっても top_k=3 以下しか返らないこと"""
        mock_firestore.add_user(uid="fb-loop-user-4", email="fb-loop-user-4@example.com")
        for i in range(5):
            mock_firestore.add_feedback("fb-loop-user-4", id=f"fb-{i}", recipe_id=f"recipe-{i}",
                                          feedback_type="cooked", tags=[], rating=4,
                                          comment=f"美味しかったけど少し物足りない感じ {i}")

        agent = ContextRetrieverAgent()
        ctx = asyncio.run(agent.retrieve(
            user_id="fb-loop-user-4",
            query_text="美味しい料理",
            top_k=3,
        ))

        assert len(ctx.similar_snippets) <= 3

    def test_empty_comment_not_added_to_corpus(self, mock_firestore):
        """空のコメントはコーパスに追加されないこと"""
        mock_firestore.add_user(uid="fb-loop-user-5", email="fb-loop-user-5@example.com")
        mock_firestore.add_feedback("fb-loop-user-5", id="fb-no-comment", recipe_id="r1",
                                      feedback_type="cooked", tags=[], rating=3, comment=None)
        mock_firestore.add_feedback("fb-loop-user-5", id="fb-empty-comment", recipe_id="r2",
                                      feedback_type="cooked", tags=[], rating=3, comment="  ")

        vector_client = InMemoryVectorSearchClient()
        agent = ContextRetrieverAgent(vector_search_client=vector_client)
        asyncio.run(agent.retrieve(user_id="fb-loop-user-5", query_text="料理"))

        assert all(s.text.strip() for s in vector_client.corpus)

    def test_reject_feedback_comment_not_seeded(self, mock_firestore):
        """reject タイプのFBは comment があってもコーパスにシードされないこと"""
        mock_firestore.add_user(uid="fb-loop-user-6", email="fb-loop-user-6@example.com")
        mock_firestore.add_feedback("fb-loop-user-6", id="fb-reject-with-comment", recipe_id="r1",
                                      feedback_type="reject", tags=["#揚げ物"],
                                      comment="揚げ物は苦手です")

        vector_client = InMemoryVectorSearchClient()
        agent = ContextRetrieverAgent(vector_search_client=vector_client)
        asyncio.run(agent.retrieve(user_id="fb-loop-user-6", query_text="揚げ物"))

        corpus_ids = [s.id for s in vector_client.corpus]
        assert "fb_comment_fb-reject-with-comment" not in corpus_ids


# ============================================================
# AC-2: 不採用タグに該当するレシピがベクトル検索から除外されること
# ============================================================

class TestNegativeTagsHardFilter:
    def test_negative_tags_exclude_matching_snippets(self, mock_firestore):
        mock_firestore.add_user(uid="neg-filter-1", email="neg-filter-1@example.com")
        mock_firestore.add_feedback("neg-filter-1", id=str(uuid.uuid4()), recipe_id="r1",
                                      feedback_type="reject", tags=["#揚げ物"])

        corpus = [
            RecipeSnippet(id="揚げ物レシピ", text="鶏の唐揚げ 揚げ物 人気レシピ", source="external_recipe", tags=["#揚げ物"]),
            RecipeSnippet(id="ヘルシーレシピ", text="鶏の照り焼き ヘルシーレシピ", source="external_recipe", tags=["焼き物"]),
        ]
        vector_client = InMemoryVectorSearchClient(corpus=corpus)
        agent = ContextRetrieverAgent(vector_search_client=vector_client)

        ctx = asyncio.run(agent.retrieve(user_id="neg-filter-1", query_text="鶏肉レシピ", top_k=5))

        ids = [s.id for s in ctx.similar_snippets]
        assert "揚げ物レシピ" not in ids
        assert "ヘルシーレシピ" in ids

    def test_multiple_negative_tags_all_excluded(self, mock_firestore):
        mock_firestore.add_user(uid="neg-filter-2", email="neg-filter-2@example.com")
        mock_firestore.add_feedback("neg-filter-2", id=str(uuid.uuid4()), recipe_id="r1",
                                      feedback_type="reject", tags=["#辛い", "#揚げ物"])

        corpus = [
            RecipeSnippet(id="辛いレシピ", text="麻婆豆腐 辛口", source="external_recipe", tags=["#辛い"]),
            RecipeSnippet(id="揚げ物レシピ", text="エビフライ", source="external_recipe", tags=["#揚げ物"]),
            RecipeSnippet(id="安全なレシピ", text="サラダチキン 低カロリー", source="external_recipe", tags=["#ヘルシー"]),
        ]
        vector_client = InMemoryVectorSearchClient(corpus=corpus)
        agent = ContextRetrieverAgent(vector_search_client=vector_client)

        ctx = asyncio.run(agent.retrieve(user_id="neg-filter-2", query_text="料理のレシピ", top_k=10))

        ids = [s.id for s in ctx.similar_snippets]
        assert "辛いレシピ" not in ids
        assert "揚げ物レシピ" not in ids
        assert "安全なレシピ" in ids

    def test_other_users_negative_tags_do_not_affect(self, mock_firestore):
        """他ユーザーの negative_tags は自分の検索に影響しないこと"""
        mock_firestore.add_user(uid="user-a-neg", email="user-a-neg@example.com")
        mock_firestore.add_user(uid="user-b-neg", email="user-b-neg@example.com")
        mock_firestore.add_feedback("user-a-neg", id=str(uuid.uuid4()), recipe_id="r1",
                                      feedback_type="reject", tags=["#揚げ物"])

        corpus = [
            RecipeSnippet(id="揚げ物", text="唐揚げレシピ", source="external_recipe", tags=["#揚げ物"]),
        ]
        vector_client = InMemoryVectorSearchClient(corpus=corpus)
        agent = ContextRetrieverAgent(vector_search_client=vector_client)

        ctx = asyncio.run(agent.retrieve(user_id="user-b-neg", query_text="揚げ物 唐揚げ", top_k=5))

        ids = [s.id for s in ctx.similar_snippets]
        assert "揚げ物" in ids


# ============================================================
# 層分離の確認
# ============================================================

class TestLayerSeparation:
    def test_hard_constraints_not_passed_to_vector_search(self, mock_firestore):
        mock_firestore.add_user(
            uid="sep-user", email="sep-user@example.com",
            preferences={"allergies": ["えび"], "dislikes": ["ナス"], "goal": "none", "kitchen_tools": []},
        )

        captured: list[str] = []

        class SpyClient:
            async def search(self, user_id, query_text, top_k, exclude_tags=()):
                captured.extend(exclude_tags)
                return []

        agent = ContextRetrieverAgent(vector_search_client=SpyClient())
        asyncio.run(agent.retrieve(user_id="sep-user", query_text="えびを使った料理", top_k=3))

        assert "えび" not in captured
        assert "ナス" not in captured
