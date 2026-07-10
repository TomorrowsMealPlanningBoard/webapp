"""
ADK Orchestrator — Google ADK Workflow を使ってエージェント間連携を制御する。

処理フロー（SPEC.md §5.2）:
  1. データ収集フェーズ（並列）: Context Retriever + Vision Analyzer を並列ノードで同時実行
  2. 生成フェーズ: Recipe Generator に集約結果を渡して3食提案を生成
  3. 監査・承認フェーズ（ループ）: Recipe Reviewer が違反チェック → 差し戻し → 再生成

ADK 移行の方針:
  - google.adk.workflow.Workflow + @node デコレータで各フェーズを定義
  - エージェント間のデータは ctx.state (session state dict) を通じて受け渡す
  - ctx.route で差し戻しループ制御
  - OpenTelemetry + Cloud Trace で span が自動計装される（main.py で TracerProvider を設定済み）
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from opentelemetry import trace

from ..schemas import MealItem, MealPlan, SuggestRequest
from .context_retriever import ContextRetrieverAgent, RetrievedContext
from .reviewer import ReviewProfile, review_recipe_with_retries
from . import recipe_generator as rg

_tracer = trace.get_tracer("tomorrows_meal.orchestrator")

# ADK imports
from google.adk.workflow import Workflow, node, START, JoinNode
from google.adk.agents.context import Context
from google.adk import Runner
from google.adk.sessions import InMemorySessionService

logger = logging.getLogger("tomorrows_meal.orchestrator")

DEFAULT_MAX_REVIEWER_RETRIES = 2


@dataclass
class OrchestratorResult:
    meal_plan: MealPlan
    message: str
    context: RetrievedContext
    vision_skipped: bool = False
    reviewer_retries: list[int] = field(default_factory=list)
    phase_durations_ms: dict[str, float] = field(default_factory=dict)


class MealOrchestrator:
    """
    Google ADK Workflow でエージェント間連携を制御するオーケストレーター。

    各フェーズを @node として定義し、Workflow の edges で実行順序・並列を宣言する。
    エージェント間のデータは ctx.state (InMemorySessionService のセッション状態) 経由で受け渡す。
    OpenTelemetry を通じた Cloud Trace への自動計装は FastAPI 起動時に設定する (app/main.py)。
    """

    def __init__(
        self,
        max_reviewer_retries: int = DEFAULT_MAX_REVIEWER_RETRIES,
    ):
        self.max_reviewer_retries = max_reviewer_retries

    # ------------------------------------------------------------------
    # フェーズ1: データ収集（並列実行）
    # ------------------------------------------------------------------

    async def _run_data_collection(
        self,
        user_id: str,
        req: SuggestRequest,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ) -> tuple[RetrievedContext, list[str]]:
        """Context Retriever と Vision Analyzer を並列実行し、結果を集約する。"""
        context_agent = ContextRetrieverAgent()
        query_text = " ".join(req.mood_tags)
        if req.mood_freetext:
            query_text = f"{query_text} {req.mood_freetext}".strip()

        if image_bytes:
            from .vision_analyzer import analyze_image

            async def run_vision() -> list[str]:
                result = await asyncio.to_thread(analyze_image, image_bytes, image_mime_type or "image/jpeg")
                return [ing.name for ing in result.ingredients]

            context_result, vision_ingredients = await asyncio.gather(
                context_agent.retrieve(user_id=user_id, query_text=query_text),
                run_vision(),
            )
        else:
            context_result = await context_agent.retrieve(user_id=user_id, query_text=query_text)
            vision_ingredients = [ing.name for ing in (req.ingredients or [])]

        return context_result, vision_ingredients

    # ------------------------------------------------------------------
    # フェーズ2: 生成フェーズ
    # ------------------------------------------------------------------

    def _run_generation(
        self,
        req: SuggestRequest,
        context: RetrievedContext,
    ) -> tuple[MealPlan, str]:
        """Recipe Generator を呼び出して3食提案を生成する。"""
        return rg.generate_meal_plan(req, context)

    # ------------------------------------------------------------------
    # フェーズ3: 監査・承認ループ
    # ------------------------------------------------------------------

    def _build_review_profile(self, context: RetrievedContext) -> ReviewProfile:
        return ReviewProfile(
            allergies=context.hard_constraints.allergies,
            negative_tags=context.structured_feedback.negative_tags,
            kitchen_tools=context.hard_constraints.available_kitchen_tools,
        )

    def _meal_item_to_recipe(self, item: MealItem):
        from ..schemas import Recipe
        return Recipe(
            id=item.id,
            title=item.title,
            emoji=item.emoji,
            description=item.description,
            cooking_time=item.cooking_time,
            effort_level=item.effort_level,
            servings=item.servings,
            tags=item.tags,
            ingredients=item.ingredients,
            steps=item.steps,
            nutrition_note=item.nutrition_note,
            required_tools=item.required_tools,
        )

    def _recipe_to_meal_item(self, recipe, meal_type: str, original: MealItem) -> MealItem:
        return MealItem(
            id=recipe.id,
            meal_type=meal_type,
            title=recipe.title,
            emoji=original.emoji,
            description=recipe.description,
            cooking_time=recipe.cooking_time,
            effort_level=recipe.effort_level,
            servings=original.servings,
            tags=recipe.tags,
            ingredients=recipe.ingredients,
            steps=original.steps,
            nutrition_note=original.nutrition_note,
            required_tools=recipe.required_tools,
        )

    def _run_review_loop(
        self,
        meal_plan: MealPlan,
        context: RetrievedContext,
        req: SuggestRequest,
    ) -> tuple[MealPlan, list[int]]:
        """各食事に対して Reviewer → 差し戻し → Generator のループを実行する。"""
        profile = self._build_review_profile(context)
        retry_counts: list[int] = []

        reviewed_meals: dict[str, MealItem] = {}
        for meal_type, item in [
            ("breakfast", meal_plan.breakfast),
            ("lunch", meal_plan.lunch),
            ("dinner", meal_plan.dinner),
        ]:
            recipe = self._meal_item_to_recipe(item)

            def make_regenerate_fn(mt: str, orig_item: MealItem):
                def regenerate_fn(failed_recipe, reasons: list[str]):
                    logger.warning(
                        "reviewer_rejected",
                        extra={"meal_type": mt, "reasons": reasons},
                    )
                    retry_req = req.model_copy(
                        update={"mood_freetext": f"以下を避けてください: {', '.join(reasons)}"}
                    )
                    new_plan, _ = rg.generate_meal_plan(retry_req, context)
                    new_item = getattr(new_plan, mt)
                    return self._meal_item_to_recipe(new_item)
                return regenerate_fn

            outcome = review_recipe_with_retries(
                recipe=recipe,
                profile=profile,
                regenerate_fn=make_regenerate_fn(meal_type, item),
                max_retries=self.max_reviewer_retries,
            )

            retry_counts.append(outcome.attempts - 1)

            if outcome.approved and outcome.recipe is not None:
                reviewed_meals[meal_type] = self._recipe_to_meal_item(outcome.recipe, meal_type, item)
            else:
                logger.error(
                    "reviewer_rejected_fallback",
                    extra={"meal_type": meal_type, "attempts": outcome.attempts},
                )
                reviewed_meals[meal_type] = item

        reviewed_plan = MealPlan(
            breakfast=reviewed_meals["breakfast"],
            lunch=reviewed_meals["lunch"],
            dinner=reviewed_meals["dinner"],
        )
        return reviewed_plan, retry_counts

    # ------------------------------------------------------------------
    # ADK Workflow を使った統合エントリポイント
    # ------------------------------------------------------------------

    async def run(
        self,
        user_id: str,
        req: SuggestRequest,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ) -> OrchestratorResult:
        """
        ADK Workflow でフェーズ1（並列）→ フェーズ2（生成）→ フェーズ3（審査）を実行する。

        セッション state のキー:
          input_user_id, input_req, input_image_bytes, input_image_mime_type  — 入力
          output_context      — Context Retriever の結果 (RetrievedContext)
          output_ingredients  — Vision Analyzer の結果 (list[str])
          output_meal_plan    — Recipe Generator の結果 (MealPlan)
          output_message      — Recipe Generator のメッセージ (str)
          output_reviewed_plan — Reviewer 通過後の MealPlan
          output_retry_counts — 各食事のリトライ回数 (list[int])
          phase_durations_ms  — 各フェーズの処理時間 (dict)
        """
        orchestrator = self

        # -------- フェーズ1: データ収集ノード定義（並列実行） --------
        #
        # 【重要 / #110 が直しきれなかった真因】
        # 以前は collect_context / collect_vision に parallel_worker=True を付け、
        # かつ generate を「両ノードの合流点（JoinNode）」ではなく素の FunctionNode
        # として ((collect_context, collect_vision), generate) で配線していた。
        # ADK 2.3.0 の Workflow スケジューラは、合流先が JoinNode
        # （_requires_all_predecessors=True）でない限り「先に完了した1つの前段ノード」
        # の完了時点で下流ノードのトリガーをバッファリングする（_buffer_downstream_triggers
        # の else 分岐）。その結果:
        #   - mood_tags 空: 層3ベクトル検索が query_text 空でスキップされ collect_context
        #     が高速完了 → レースが顕在化せず 200。
        #   - mood_tags 非空: 層3（本番 Memory Bank）が ~7秒かかる間に、画像なしで
        #     即完了する collect_vision が先に generate をトリガー → generate が
        #     output_context 未設定を検知して防御 RuntimeError（=500, 処理時間~7.8秒）。
        # これは例外種別（CancelledError 等）でも state 非伝播でもなく、
        # 「合流バリアが無かったこと」によるスケジューリング競合が真因。
        # #110 の失敗注入テストは search() を「同期・即時例外」にしていたため
        # collect_context がフォールバックで即完了してしまい、この遅延レースを
        # 再現できていなかった（＝テストは通るのに本番で直らなかった理由）。
        #
        # 【根治】generate の直前に JoinNode（collect_join）を挟み、collect_context と
        # collect_vision の両方が完了するまで generate を起動しないバリアを張る。
        # あわせて parallel_worker=True を撤去する（START からの fan-out で既に並列に
        # 走るため冗長で、_ParallelWorker のサブブランチ化はむしろ複雑さを増すだけ）。
        # 層3（Memory Bank）のハング/失敗は context_retriever 側で timeout+フォールバック
        # 済みのため、collect_context は必ず output_context を設定して完了する。
        @node
        async def collect_context(ctx: Context) -> None:
            t0 = time.perf_counter()
            user_id_ = ctx.state["input_user_id"]
            req_ = ctx.state["input_req"]

            with _tracer.start_as_current_span("collect_context") as span:
                span.set_attribute("user_id", user_id_)
                span.set_attribute("mood_tags", str(req_.mood_tags))

                context_agent = ContextRetrieverAgent()
                query_text = " ".join(req_.mood_tags)
                if req_.mood_freetext:
                    query_text = f"{query_text} {req_.mood_freetext}".strip()

                # 層3（ベクトル検索/Memory Bank）は context_retriever 側で
                # timeout + BaseException フォールバック済み。ここでの try/except は
                # 層1/層2 のDB取得やユーザー不在等の真因を surface するための保険。
                # 万一 BaseException（CancelledError 含む）が来ても真の traceback を
                # 記録した上で再送出し、握り潰しによる二次被害（generate 側の曖昧な
                # KeyError）を防ぐ。
                try:
                    retrieved = await context_agent.retrieve(
                        user_id=user_id_, query_text=query_text
                    )
                except BaseException:
                    logger.exception(
                        "collect_context のコンテキスト取得に失敗しました (user_id=%s)。"
                        "真の例外を記録し再送出します。",
                        user_id_,
                    )
                    span.record_exception(sys.exc_info()[1])
                    raise

                duration_ms = (time.perf_counter() - t0) * 1000

                span.set_attribute("duration_ms", duration_ms)
                span.set_attribute("allergen_count", len(retrieved.hard_constraints.allergies))
                span.set_attribute("similar_snippets_count", len(retrieved.similar_snippets))

            ctx.state["output_context"] = retrieved
            ctx.state.setdefault("phase_durations_ms", {})["context_retrieval_ms"] = duration_ms
            logger.info(
                "adk_node_context_retrieved",
                extra={"user_id": user_id_, "duration_ms": duration_ms},
            )

        @node
        async def collect_vision(ctx: Context) -> None:
            t0 = time.perf_counter()
            req_ = ctx.state["input_req"]
            img = ctx.state.get("input_image_bytes")
            mime = ctx.state.get("input_image_mime_type")

            with _tracer.start_as_current_span("collect_vision") as span:
                span.set_attribute("used_image", bool(img))

                if img:
                    from .vision_analyzer import analyze_image
                    result = await asyncio.to_thread(analyze_image, img, mime or "image/jpeg")
                    ingredients = [ing.name for ing in result.ingredients]
                else:
                    ingredients = [ing.name for ing in (req_.ingredients or [])]

                duration_ms = (time.perf_counter() - t0) * 1000
                span.set_attribute("ingredient_count", len(ingredients))
                span.set_attribute("duration_ms", duration_ms)

            ctx.state["output_ingredients"] = ingredients
            ctx.state.setdefault("phase_durations_ms", {})["vision_ms"] = duration_ms
            logger.info(
                "adk_node_vision_done",
                extra={"ingredient_count": len(ingredients), "used_image": bool(img)},
            )

        # -------- フェーズ2: 生成ノード定義 --------
        @node
        async def generate(ctx: Context) -> None:
            t0 = time.perf_counter()
            req_ = ctx.state["input_req"]
            # 防御的コード: 通常 collect_context が必ず output_context を設定する
            # （層3失敗時もフォールバックで RetrievedContext を返す）。それでも
            # collect_context 自体が例外送出で未設定になった場合、ここで曖昧な
            # KeyError: 'output_context' を出さず、真因が分かる明示的なエラーにする。
            context_ = ctx.state.get("output_context")
            if context_ is None:
                raise RuntimeError(
                    "コンテキスト取得（collect_context）が完了していないため生成できません"
                    "（output_context 未設定）。collect_context のログで真の例外を確認してください。"
                )
            ingredients = ctx.state.get("output_ingredients", [])
            import os as _os
            model_name = _os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

            with _tracer.start_as_current_span("generate") as span:
                span.set_attribute("model_name", model_name)
                span.set_attribute("ingredient_count", len(ingredients))

                # Vision で取得した食材を req に反映
                if ingredients and not req_.ingredients:
                    from ..schemas import IngredientItem
                    req_ = req_.model_copy(update={
                        "ingredients": [
                            IngredientItem(name=name, quantity=None, unit="", freshness="unknown")
                            for name in ingredients
                        ]
                    })
                    ctx.state["input_req"] = req_

                meal_plan, message = orchestrator._run_generation(req_, context_)
                duration_ms = (time.perf_counter() - t0) * 1000
                span.set_attribute("duration_ms", duration_ms)

            ctx.state["output_meal_plan"] = meal_plan
            ctx.state["output_message"] = message
            ctx.state.setdefault("phase_durations_ms", {})["generation_ms"] = duration_ms
            logger.info(
                "adk_node_generation_done",
                extra={"duration_ms": duration_ms},
            )

        # -------- フェーズ3: 審査ノード定義 --------
        @node
        async def review(ctx: Context) -> None:
            t0 = time.perf_counter()
            req_ = ctx.state["input_req"]
            # 防御的コード（generate と同様）: output_context 未設定時は曖昧な KeyError で
            # 二次被害にせず、真因が分かる明示的なエラーにする。
            context_ = ctx.state.get("output_context")
            if context_ is None:
                raise RuntimeError(
                    "コンテキスト取得（collect_context）が完了していないため審査できません"
                    "（output_context 未設定）。collect_context のログで真の例外を確認してください。"
                )
            meal_plan = ctx.state["output_meal_plan"]

            with _tracer.start_as_current_span("review") as span:
                span.set_attribute("user_id", user_id)
                span.set_attribute("max_retries", orchestrator.max_reviewer_retries)

                reviewed_plan, retry_counts = orchestrator._run_review_loop(meal_plan, context_, req_)
                duration_ms = (time.perf_counter() - t0) * 1000

                span.set_attribute("retry_counts", str(retry_counts))
                span.set_attribute("total_retries", sum(retry_counts))
                span.set_attribute("duration_ms", duration_ms)

            ctx.state["output_reviewed_plan"] = reviewed_plan
            ctx.state["output_retry_counts"] = retry_counts
            ctx.state.setdefault("phase_durations_ms", {})["review_ms"] = duration_ms
            logger.info(
                "adk_node_review_done",
                extra={
                    "retry_counts": retry_counts,
                    "total_retries": sum(retry_counts),
                    "duration_ms": duration_ms,
                },
            )

        # -------- Workflow 定義 --------
        # フェーズ1: collect_context と collect_vision を並列実行
        # フェーズ2: 【バリア】collect_join(JoinNode) で両ノードの完了を待ってから generate
        # フェーズ3: generate 完了後 review を実行
        #
        # collect_join は JoinNode（_requires_all_predecessors=True）。
        # これにより generate は「両方の前段ノードが COMPLETED になるまで」起動しない。
        # 素の FunctionNode を合流点にすると「先に終わった片方」で下流が起動してしまう
        # （= mood_tags 非空 500 の真因）。バリアを JoinNode にするのが根治。
        collect_join = JoinNode(name="collect_join")
        workflow = Workflow(
            name="meal_planning_workflow",
            edges=[
                (START, (collect_context, collect_vision)),
                ((collect_context, collect_vision), collect_join),
                (collect_join, generate),
                (generate, review),
            ],
        )

        # -------- Runner でセッションを作り実行 --------
        session_service = InMemorySessionService()
        session = await session_service.create_session(
            app_name="tomorrows_meal",
            user_id=user_id,
            state={
                "input_user_id": user_id,
                "input_req": req,
                "input_image_bytes": image_bytes,
                "input_image_mime_type": image_mime_type,
                "phase_durations_ms": {},
            },
        )

        t_total = time.perf_counter()
        runner = Runner(
            node=workflow,
            session_service=session_service,
            app_name="tomorrows_meal",
        )
        async for _ in runner.run_async(user_id=user_id, session_id=session.id):
            pass  # イベントは ADK のトレース計装に流れる

        # session オブジェクトはキャッシュされたままなので、最新状態を再取得する
        updated_session = await session_service.get_session(
            app_name="tomorrows_meal", user_id=user_id, session_id=session.id
        )
        final_state = updated_session.state

        phase_durations_ms = dict(final_state.get("phase_durations_ms") or {})
        phase_durations_ms["total_ms"] = (time.perf_counter() - t_total) * 1000

        # data_collection_ms は context + vision の並列実行なので max で表現
        ctx_ms = phase_durations_ms.get("context_retrieval_ms", 0)
        vis_ms = phase_durations_ms.get("vision_ms", 0)
        phase_durations_ms["data_collection_ms"] = max(ctx_ms, vis_ms)
        phase_durations_ms.setdefault("generation_ms", 0)
        phase_durations_ms.setdefault("review_ms", 0)

        reviewed_plan = final_state.get("output_reviewed_plan")
        meal_plan_fallback = final_state.get("output_meal_plan")

        logger.info(
            "orchestrator_completed",
            extra={
                "user_id": user_id,
                "total_ms": phase_durations_ms["total_ms"],
                "retry_counts": final_state.get("output_retry_counts", []),
            },
        )

        return OrchestratorResult(
            meal_plan=reviewed_plan or meal_plan_fallback,
            message=final_state.get("output_message") or "",
            context=final_state.get("output_context"),
            vision_skipped=image_bytes is None,
            reviewer_retries=list(final_state.get("output_retry_counts") or []),
            phase_durations_ms=phase_durations_ms,
        )
