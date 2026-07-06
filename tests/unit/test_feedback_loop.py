"""
Issue #21: 過去のフィードバックを次回の提案プロンプトへ反映する処理のユニットテスト

AC対応:
1. Context Retriever Agent が negative_tags（ハードフィルタ）、positive_tags（参考情報）、
   free_text（ベクトル検索上位3件）をプロンプトに注入すること
2. 不採用タグに該当するレシピ属性が提案に含まれないこと（決定的フィルタ）
3. uv run pytest tests/unit/test_feedback_loop.py が全件パスすること
"""
import asyncio
import uuid

import pytest

from app.agents.context_retriever import (
    ContextRetrieverAgent,
    InMemoryVectorSearchClient,
    RecipeSnippet,
)
from app.models import Feedback, User
from app.auth import get_password_hash


# ------------------------------------------------------------------ helpers --

def _make_user(db, uid="feedback-loop-user", allergies=None, dislikes=None):
    user = User(
        uid=uid,
        email=f"{uid}@example.com",
        hashed_password=get_password_hash("testpassword"),
        display_name="フィードバックループテストユーザー",
        preferences={
            "allergies": allergies or [],
            "dislikes": dislikes or [],
            "goal": "other",
            "kitchen_tools": [],
        },
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _add_reject_feedback(db, user_id, tags, recipe_id="dummy-recipe"):
    fb = Feedback(
        id=str(uuid.uuid4()),
        user_id=user_id,
        recipe_id=recipe_id,
        feedback_type="reject",
        tags=tags,
    )
    db.add(fb)
    db.commit()
    return fb


def _add_cooked_feedback(db, user_id, tags, rating=4, comment=None, recipe_id="dummy-recipe"):
    fb = Feedback(
        id=str(uuid.uuid4()),
        user_id=user_id,
        recipe_id=recipe_id,
        feedback_type="cooked",
        tags=tags,
        rating=rating,
        comment=comment,
    )
    db.add(fb)
    db.commit()
    return fb


# ============================================================
# AC-1: negative_tags / positive_tags / free_text がコンテキストに注入されること
# ============================================================

class TestFeedbackInjectedToContext:
    def test_negative_tags_injected_to_context(self, db):
        """不採用FBのタグが negative_tags として RetrievedContext に含まれること"""
        user = _make_user(db)
        _add_reject_feedback(db, user.uid, tags=["#揚げ物", "#辛い"])

        agent = ContextRetrieverAgent(db=db)
        ctx = asyncio.run(agent.retrieve(user_id=user.uid))

        assert "#揚げ物" in ctx.structured_feedback.negative_tags
        assert "#辛い" in ctx.structured_feedback.negative_tags

    def test_positive_tags_injected_to_context(self, db):
        """調理後FBのタグが positive_tags として RetrievedContext に含まれること"""
        user = _make_user(db)
        _add_cooked_feedback(db, user.uid, tags=["味付けが最高", "手軽だった"], rating=5)

        agent = ContextRetrieverAgent(db=db)
        ctx = asyncio.run(agent.retrieve(user_id=user.uid))

        assert "味付けが最高" in ctx.structured_feedback.positive_tags
        assert "手軽だった" in ctx.structured_feedback.positive_tags

    def test_free_text_comment_seeded_to_vector_corpus(self, db):
        """
        自由記述FB（comment）がベクトル検索コーパスにシードされ、
        retrieve() で類似スニペットとして返ること（AC §1 free_text → ベクトル検索上位3件）
        """
        user = _make_user(db)
        _add_cooked_feedback(
            db, user.uid,
            tags=["手軽だった"],
            rating=5,
            comment="もう少し塩気が欲しかった。薄味の料理だった",
        )

        agent = ContextRetrieverAgent(db=db)
        ctx = asyncio.run(agent.retrieve(
            user_id=user.uid,
            query_text="塩気のある料理を作りたい",
            top_k=3,
        ))

        assert len(ctx.similar_snippets) >= 1
        ids = [s.id for s in ctx.similar_snippets]
        assert any("fb_comment_" in sid for sid in ids)

    def test_free_text_limited_to_top_k(self, db):
        """自由記述FBが複数あっても top_k=3 以下しか返らないこと"""
        user = _make_user(db)
        for i in range(5):
            _add_cooked_feedback(
                db, user.uid,
                tags=[],
                rating=4,
                comment=f"美味しかったけど少し物足りない感じ {i}",
                recipe_id=f"recipe-{i}",
            )

        agent = ContextRetrieverAgent(db=db)
        ctx = asyncio.run(agent.retrieve(
            user_id=user.uid,
            query_text="美味しい料理",
            top_k=3,
        ))

        assert len(ctx.similar_snippets) <= 3

    def test_empty_comment_not_added_to_corpus(self, db):
        """空のコメントはコーパスに追加されないこと"""
        user = _make_user(db)
        _add_cooked_feedback(db, user.uid, tags=[], rating=3, comment=None)
        _add_cooked_feedback(db, user.uid, tags=[], rating=3, comment="  ", recipe_id="recipe-2")

        client = InMemoryVectorSearchClient()
        agent = ContextRetrieverAgent(db=db, vector_search_client=client)
        asyncio.run(agent.retrieve(user_id=user.uid, query_text="料理"))

        assert all(s.text.strip() for s in client.corpus)

    def test_reject_feedback_comment_not_seeded(self, db):
        """
        reject タイプのFBは comment があってもコーパスにシードされないこと
        (reject は negative_tags による構造化フィルタで処理する)
        """
        user = _make_user(db)
        # reject FBにcommentを付与（通常UI上は発生しないが、データ整合性テスト）
        fb = Feedback(
            id="fb-reject-with-comment",
            user_id=user.uid,
            recipe_id="recipe-dummy",
            feedback_type="reject",
            tags=["#揚げ物"],
            comment="揚げ物は苦手です",
        )
        db.add(fb)
        db.commit()

        client = InMemoryVectorSearchClient()
        agent = ContextRetrieverAgent(db=db, vector_search_client=client)
        asyncio.run(agent.retrieve(user_id=user.uid, query_text="揚げ物"))

        corpus_ids = [s.id for s in client.corpus]
        assert "fb_comment_fb-reject-with-comment" not in corpus_ids


# ============================================================
# AC-2: 不採用タグに該当するレシピがベクトル検索から除外されること（決定的フィルタ）
# ============================================================

class TestNegativeTagsHardFilter:
    def test_negative_tags_exclude_matching_snippets(self, db):
        """
        不採用FBで蓄積された negative_tags に該当するタグを持つスニペットが
        ベクトル検索から除外されること（ハイブリッド検索のハードフィルタ）
        """
        user = _make_user(db)
        _add_reject_feedback(db, user.uid, tags=["#揚げ物"])

        corpus = [
            RecipeSnippet(id="揚げ物レシピ", text="鶏の唐揚げ 揚げ物 人気レシピ", source="external_recipe", tags=["#揚げ物"]),
            RecipeSnippet(id="ヘルシーレシピ", text="鶏の照り焼き ヘルシーレシピ", source="external_recipe", tags=["焼き物"]),
        ]
        client = InMemoryVectorSearchClient(corpus=corpus)
        agent = ContextRetrieverAgent(db=db, vector_search_client=client)

        ctx = asyncio.run(agent.retrieve(user_id=user.uid, query_text="鶏肉レシピ", top_k=5))

        ids = [s.id for s in ctx.similar_snippets]
        assert "揚げ物レシピ" not in ids
        assert "ヘルシーレシピ" in ids

    def test_multiple_negative_tags_all_excluded(self, db):
        """複数の不採用タグが全て除外対象となること"""
        user = _make_user(db)
        _add_reject_feedback(db, user.uid, tags=["#辛い", "#揚げ物"])

        corpus = [
            RecipeSnippet(id="辛いレシピ", text="麻婆豆腐 辛口", source="external_recipe", tags=["#辛い"]),
            RecipeSnippet(id="揚げ物レシピ", text="エビフライ", source="external_recipe", tags=["#揚げ物"]),
            RecipeSnippet(id="安全なレシピ", text="サラダチキン 低カロリー", source="external_recipe", tags=["#ヘルシー"]),
        ]
        client = InMemoryVectorSearchClient(corpus=corpus)
        agent = ContextRetrieverAgent(db=db, vector_search_client=client)

        ctx = asyncio.run(agent.retrieve(user_id=user.uid, query_text="料理のレシピ", top_k=10))

        ids = [s.id for s in ctx.similar_snippets]
        assert "辛いレシピ" not in ids
        assert "揚げ物レシピ" not in ids
        assert "安全なレシピ" in ids

    def test_negative_tags_filter_is_deterministic_not_probabilistic(self, db):
        """
        negative_tags によるフィルタは決定的（if文による除外）であり、
        スコアに関わらず必ず除外されること（確率的なベクトル類似度に依存しない）
        """
        user = _make_user(db)
        _add_reject_feedback(db, user.uid, tags=["#豚肉"])

        # クエリと完全一致する高スコアのスニペットでも除外される
        corpus = [
            RecipeSnippet(
                id="豚肉最高スコア",
                text="豚肉 豚肉 豚肉 豚肉 豚肉 豚肉",  # クエリに高マッチ
                source="external_recipe",
                tags=["#豚肉"],
            ),
        ]
        client = InMemoryVectorSearchClient(corpus=corpus)
        agent = ContextRetrieverAgent(db=db, vector_search_client=client)

        ctx = asyncio.run(agent.retrieve(user_id=user.uid, query_text="豚肉を使った料理", top_k=5))

        assert len(ctx.similar_snippets) == 0

    def test_other_users_negative_tags_do_not_affect(self, db):
        """他ユーザーの negative_tags は自分の検索に影響しないこと"""
        user_a = _make_user(db, uid="user-a-neg")
        user_b = _make_user(db, uid="user-b-neg")
        _add_reject_feedback(db, user_a.uid, tags=["#揚げ物"])

        corpus = [
            RecipeSnippet(id="揚げ物", text="唐揚げレシピ", source="external_recipe", tags=["#揚げ物"]),
        ]
        client = InMemoryVectorSearchClient(corpus=corpus)
        agent = ContextRetrieverAgent(db=db, vector_search_client=client)

        # user_b には揚げ物の reject がないので除外されない
        ctx = asyncio.run(agent.retrieve(user_id=user_b.uid, query_text="揚げ物 唐揚げ", top_k=5))

        ids = [s.id for s in ctx.similar_snippets]
        assert "揚げ物" in ids


# ============================================================
# AC-2: 不採用タグは層1（ハード制約）と混在しないこと（設計整合性）
# ============================================================

class TestLayerSeparation:
    def test_hard_constraints_not_passed_to_vector_search(self, db):
        """
        層1（アレルギー・禁止食材）はベクトル検索の exclude_tags に渡らないこと。
        not_negative_tags による除外は層2（negative_tags）のみが担う（SPEC.md §3）。
        """
        user = _make_user(db, allergies=["えび"], dislikes=["ナス"])

        captured: list[str] = []

        class SpyClient:
            async def search(self, user_id, query_text, top_k, exclude_tags=()):
                captured.extend(exclude_tags)
                return []

        agent = ContextRetrieverAgent(db=db, vector_search_client=SpyClient())
        asyncio.run(agent.retrieve(user_id=user.uid, query_text="えびを使った料理", top_k=3))

        assert "えび" not in captured
        assert "ナス" not in captured
