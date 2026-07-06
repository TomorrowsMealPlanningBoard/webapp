"""
Recipe Reviewer Agent — 生成された3案を決定的（if文ベース）に監査し、
違反があれば理由付きで Generator に差し戻すガードレール。

設計原則（SPEC.md §3 層1 / Issue #30）:
    - 層1（アレルギー・禁止食材・調理器具）の検査にベクトル検索・確率的判定を使わない。
    - 文字列の完全一致・部分一致による機械的な除外のみで判定する。

ADK未導入時点の実装方針:
    このリポジトリには現時点で ADK（Agent Development Kit）が導入されていない
    （app/agents/vision_analyzer.py も素の関数として実装されている）。
    そのため本モジュールも「素の Python 関数 + ループ」で差し戻し制御を実装するが、
    将来 ADK の LoopAgent 等に載せ替えられるよう、
        - 監査ロジック（check_recipe）
        - ループ制御（review_recipes）
        - 再生成コールバック（RegenerateFn）
    を明確に分離している。ADK導入後は review_recipes の「ループ制御」部分を
    ADK の LoopAgent / Runner に置き換え、check_recipe はそのままツール関数として
    再利用できる想定。

スコープ:
    Recipe Generator / Context Retriever / Orchestrator（#11, #29, #31）の実装には
    踏み込まない。Generator への「差し戻し」は regenerate_fn: Callable として
    呼び出し側から注入される（実際の Generator が未実装でもモックで検証可能）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from opentelemetry import trace

_tracer = trace.get_tracer("tomorrows_meal.reviewer")

from app.schemas import Recipe

# 再生成コールバックのシグネチャ:
#   (前回のレシピ案, 違反理由のリスト) -> 再生成された新しいレシピ案
RegenerateFn = Callable[[Recipe, List[str]], Recipe]

DEFAULT_MAX_RETRIES = 2


class ViolationType(str, Enum):
    ALLERGEN = "allergen"
    NEGATIVE_TAG = "negative_tag"
    MISSING_TOOL = "missing_tool"


@dataclass
class ReviewProfile:
    """Reviewer が検査に用いるハード制約プロファイル（層1 + 層2の除外情報）。

    Context Retriever Agent（#29）が将来これを構築する想定だが、
    本チケットのスコープでは呼び出し側が任意に組み立てて渡す。
    """

    allergies: List[str] = field(default_factory=list)       # 層1: アレルギー物質
    negative_tags: List[str] = field(default_factory=list)   # 層2: 除外指定タグ（味付け・食材の不採用タグ）
    kitchen_tools: List[str] = field(default_factory=list)   # 層1: 所持している調理器具


@dataclass
class Violation:
    type: ViolationType
    reason: str


@dataclass
class RecipeCheckResult:
    recipe: Recipe
    violations: List[Violation] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.violations) == 0


@dataclass
class RecipeReviewOutcome:
    """1つのレシピ案について、差し戻しループを経た最終結果。"""

    recipe: Optional[Recipe]        # 承認されたレシピ（フォールバックで棄却された場合は None）
    approved: bool
    attempts: int                   # 検査した回数（初回検査を含む）
    violations_history: List[List[Violation]] = field(default_factory=list)
    fallback_used: bool = False


@dataclass
class ReviewSessionResult:
    """3案すべてに対する監査結果。"""

    outcomes: List[RecipeReviewOutcome]

    @property
    def all_approved(self) -> bool:
        return all(o.approved for o in self.outcomes)

    @property
    def approved_recipes(self) -> List[Recipe]:
        return [o.recipe for o in self.outcomes if o.approved and o.recipe is not None]


def _normalize(text: str) -> str:
    return text.strip().lower()


def _contains_keyword(haystack: str, keyword: str) -> bool:
    """決定的な部分一致判定（大小文字・前後空白の差異のみ吸収する）。"""
    if not keyword:
        return False
    return _normalize(keyword) in _normalize(haystack)


def check_recipe(recipe: Recipe, profile: ReviewProfile) -> RecipeCheckResult:
    """1つのレシピ案を層1のハード制約に対して決定的に検査する。

    if文ベースの機械的な文字列マッチングのみを使用し、
    ベクトル検索・埋め込み類似度などの確率的処理は一切行わない。
    """
    violations: List[Violation] = []

    searchable_fields = [recipe.title, recipe.description] + list(recipe.ingredients) + list(recipe.tags)
    searchable_text = " ".join(searchable_fields)

    # 1. アレルギー物質の混入チェック
    for allergen in profile.allergies:
        if _contains_keyword(searchable_text, allergen):
            violations.append(
                Violation(
                    type=ViolationType.ALLERGEN,
                    reason=f"アレルギー物質「{allergen}」の混入が疑われます",
                )
            )

    # 2. 除外指定タグ（negative_tags）の含有チェック
    for tag in profile.negative_tags:
        tag_hit = any(_normalize(tag) == _normalize(t) for t in recipe.tags)
        text_hit = _contains_keyword(searchable_text, tag)
        if tag_hit or text_hit:
            violations.append(
                Violation(
                    type=ViolationType.NEGATIVE_TAG,
                    reason=f"除外指定タグ「{tag}」に該当します",
                )
            )

    # 3. 未所持の調理器具の使用チェック
    owned_tools = {_normalize(t) for t in profile.kitchen_tools}
    for tool in recipe.required_tools:
        if _normalize(tool) not in owned_tools:
            violations.append(
                Violation(
                    type=ViolationType.MISSING_TOOL,
                    reason=f"未所持の調理器具「{tool}」の使用が必要です",
                )
            )

    return RecipeCheckResult(recipe=recipe, violations=violations)


def review_recipe_with_retries(
    recipe: Recipe,
    profile: ReviewProfile,
    regenerate_fn: RegenerateFn,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> RecipeReviewOutcome:
    """1つのレシピ案について、違反があれば regenerate_fn を呼んで再生成し、
    制約をクリアするまで（または最大リトライ回数に達するまで）ループする。

    ADKのLoop制御に相当する差し戻しループ本体（現状は素のPythonループで実装）。

    フォールバック挙動（上限到達時）:
        max_retries 回再生成しても違反が解消しない場合、そのレシピ案は
        「承認しない（approved=False, recipe=None）」として扱う。
        安全設計（層1をすり抜けさせない）を優先し、違反を含んだまま
        フロントエンドへ返すことは絶対に行わない。
    """
    with _tracer.start_as_current_span("review_recipe_with_retries") as span:
        span.set_attribute("recipe_title", recipe.title)
        span.set_attribute("max_retries", max_retries)

        current_recipe = recipe
        violations_history: List[List[Violation]] = []
        attempts = 0

        for attempt in range(max_retries + 1):
            attempts = attempt + 1
            result = check_recipe(current_recipe, profile)
            violations_history.append(result.violations)

            if result.is_valid:
                span.set_attribute("approved", True)
                span.set_attribute("attempts", attempts)
                span.set_attribute("rejection_reasons", [])
                return RecipeReviewOutcome(
                    recipe=current_recipe,
                    approved=True,
                    attempts=attempts,
                    violations_history=violations_history,
                    fallback_used=False,
                )

            if attempt >= max_retries:
                # 上限到達 → フォールバック: 承認せず棄却する
                all_reasons = [v.reason for v in result.violations]
                span.set_attribute("approved", False)
                span.set_attribute("attempts", attempts)
                span.set_attribute("rejection_reasons", str(all_reasons))
                span.set_attribute("fallback_used", True)
                return RecipeReviewOutcome(
                    recipe=None,
                    approved=False,
                    attempts=attempts,
                    violations_history=violations_history,
                    fallback_used=True,
                )

            reasons = [v.reason for v in result.violations]
            span.add_event(
                "recipe_rejected",
                {"attempt": attempt, "reasons": str(reasons)},
            )
            current_recipe = regenerate_fn(current_recipe, reasons)

        # 理論上到達しない（ループは必ず上記のいずれかで return する）
        span.set_attribute("approved", False)
        span.set_attribute("attempts", attempts)
        span.set_attribute("fallback_used", True)
        return RecipeReviewOutcome(
            recipe=None,
            approved=False,
            attempts=attempts,
            violations_history=violations_history,
            fallback_used=True,
        )


def review_recipes(
    recipes: List[Recipe],
    profile: ReviewProfile,
    regenerate_fn: RegenerateFn,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> ReviewSessionResult:
    """3案（複数案）すべてに対して差し戻しループを適用する。

    全案が制約をクリアして初めてフロントエンドへレスポンス可能になる
    （呼び出し側は ReviewSessionResult.all_approved を見てレスポンス可否を判断する）。
    """
    outcomes = [
        review_recipe_with_retries(
            recipe=recipe,
            profile=profile,
            regenerate_fn=regenerate_fn,
            max_retries=max_retries,
        )
        for recipe in recipes
    ]
    return ReviewSessionResult(outcomes=outcomes)
