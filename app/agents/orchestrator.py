"""
ADK Orchestrator — 4エージェントの並列実行・生成監査ループを制御する。

処理フロー（SPEC.md §5.2）:
  1. データ収集フェーズ（並列）: Context Retriever + Vision Analyzer を asyncio.gather で同時実行
  2. 生成フェーズ: Recipe Generator に集約結果 + 条件を渡して3食提案を生成
  3. 監査・承認フェーズ（ループ）: Recipe Reviewer が違反チェック → 差し戻し → 再生成

設計原則:
  - 全エージェントを同一プロセス内で引数渡しにより連携（マイクロサービス化しない）
  - Vision Analyzer の画像は任意（Noneの場合はスキップ）
  - 各フェーズの処理時間・リトライ回数はログに記録（トレーサビリティ）
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from ..schemas import MealItem, MealPlan, SuggestRequest
from .context_retriever import ContextRetrieverAgent, RetrievedContext
from .reviewer import ReviewProfile, review_recipe_with_retries
from . import recipe_generator as rg

logger = logging.getLogger("tomorrows_meal.orchestrator")

DEFAULT_MAX_REVIEWER_RETRIES = 2


@dataclass
class OrchestratorResult:
    meal_plan: MealPlan
    message: str
    context: RetrievedContext
    vision_skipped: bool = False
    reviewer_retries: list[int] = field(default_factory=list)  # 各食事のリトライ回数
    phase_durations_ms: dict[str, float] = field(default_factory=dict)


class MealOrchestrator:
    """
    4エージェントを1プロセス内で制御するオーケストレーター。
    ADK の LoopAgent / ParallelAgent に相当する制御を素の asyncio で実装し、
    将来 ADK に載せ替えられる設計としている。
    """

    def __init__(
        self,
        db: Session,
        max_reviewer_retries: int = DEFAULT_MAX_REVIEWER_RETRIES,
    ):
        self.db = db
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
        """
        Context Retriever と Vision Analyzer を並列実行し、結果を集約する。
        Vision の画像がない場合は食材リストを req.ingredients から構築する。
        """
        context_agent = ContextRetrieverAgent(db=self.db)
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
        """MealItem → reviewer が扱う Recipe 型に変換する。"""
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
        """reviewer が返した Recipe を MealItem に戻す（steps・emoji等はオリジナルを引き継ぐ）。"""
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
        """
        各食事に対して Reviewer → 差し戻し → Generator の差し戻しループを実行する。
        """
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
                    # 差し戻し: 違反理由をフリーテキストに追加して再生成
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
                # 棄却された場合はオリジナルをそのまま使用（最低限の返却を保証）
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
    # 統合エントリポイント
    # ------------------------------------------------------------------

    async def run(
        self,
        user_id: str,
        req: SuggestRequest,
        image_bytes: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ) -> OrchestratorResult:
        """
        4エージェントを順次・並列・ループで協調させてエンドツーエンドの処理を実行する。
        """
        phase_durations: dict[str, float] = {}

        # フェーズ1: データ収集（並列）
        t0 = time.perf_counter()
        context, vision_ingredients = await self._run_data_collection(
            user_id=user_id,
            req=req,
            image_bytes=image_bytes,
            image_mime_type=image_mime_type,
        )
        phase_durations["data_collection_ms"] = (time.perf_counter() - t0) * 1000

        # Vision で取得した食材を req に反映（名前だけの簡易 IngredientItem として）
        if vision_ingredients and not req.ingredients:
            from ..schemas import IngredientItem
            req = req.model_copy(update={
                "ingredients": [
                    IngredientItem(name=name, quantity=None, unit="", freshness="unknown")
                    for name in vision_ingredients
                ]
            })

        logger.info(
            "orchestrator_phase_data_collection",
            extra={
                "user_id": user_id,
                "duration_ms": phase_durations["data_collection_ms"],
                "vision_skipped": image_bytes is None,
                "ingredient_count": len(req.ingredients or []),
            },
        )

        # フェーズ2: 生成
        t1 = time.perf_counter()
        meal_plan, message = self._run_generation(req, context)
        phase_durations["generation_ms"] = (time.perf_counter() - t1) * 1000

        logger.info(
            "orchestrator_phase_generation",
            extra={
                "user_id": user_id,
                "duration_ms": phase_durations["generation_ms"],
            },
        )

        # フェーズ3: 監査ループ
        t2 = time.perf_counter()
        reviewed_plan, retry_counts = self._run_review_loop(meal_plan, context, req)
        phase_durations["review_ms"] = (time.perf_counter() - t2) * 1000

        logger.info(
            "orchestrator_phase_review",
            extra={
                "user_id": user_id,
                "duration_ms": phase_durations["review_ms"],
                "retry_counts": retry_counts,
                "total_retries": sum(retry_counts),
            },
        )

        return OrchestratorResult(
            meal_plan=reviewed_plan,
            message=message,
            context=context,
            vision_skipped=image_bytes is None,
            reviewer_retries=retry_counts,
            phase_durations_ms=phase_durations,
        )
