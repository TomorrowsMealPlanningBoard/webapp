"""
Recipe Generator Agent — Gemini を使って3つの候補レシピを Structured Outputs で生成する。

設計方針（SPEC.md §5.2 に準拠）:
- Context Retriever Agent（#29）の出力（RetrievedContext）を受け取り、
  ユーザーのプロファイル・フィードバック履歴・冷蔵庫食材をプロンプトに注入する。
- 1回の食事（朝・昼・夕のどれか）に対して3つの候補レシピを生成し、ユーザーが選ぶ。
  ※「3食提案」は「朝昼夜を全部生成」ではなく「1食分を3案提案してユーザーが選ぶ」が正仕様。
- 環境変数 GEMINI_MODEL でモデルを切り替え可能にする（デフォルト: gemini-3.1-flash-lite）。
- LLM呼び出しが失敗した場合は RuntimeError を送出し、呼び出し側でモックにフォールバックする。

層1（アレルギー・禁止食材）の扱い:
- プロンプトに「絶対に使用しない」と明記することで LLM レベルで除外を指示する。
- ただし LLM は確率的処理のため、最終的な安全チェックは Recipe Reviewer Agent（#30）が担う。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import List

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from opentelemetry import trace

from ..prompt_loader import load_prompt
from ..schemas import Recipe, RecipeStep, SuggestRequest
from .context_retriever import RetrievedContext

_PROMPT_NAME = "suggest"
_DEFAULT_MODEL = "gemini-3.1-flash-lite"

_tracer = trace.get_tracer("tomorrows_meal.recipe_generator")


def _get_client() -> genai.Client:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT 環境変数が設定されていません")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    return genai.Client(vertexai=True, project=project, location=location)


def _get_model_name() -> str:
    return os.getenv("GEMINI_MODEL", _DEFAULT_MODEL)


def _build_prompt(req: SuggestRequest, context: RetrievedContext) -> str:
    """プロンプトテンプレートにコンテキストを埋め込んで最終プロンプトを構築する。"""
    prompt_template = load_prompt(_PROMPT_NAME)

    allergies = context.hard_constraints.allergies
    allergies_str = "、".join(allergies) if allergies else "なし"

    forbidden = context.hard_constraints.forbidden_ingredients
    forbidden_str = "、".join(forbidden) if forbidden else "なし"

    tools = context.hard_constraints.available_kitchen_tools
    tools_str = "、".join(tools) if tools else "包丁・フライパン・鍋（基本装備のみ）"

    goal_str = "特になし（バランス良く）"

    if req.ingredients:
        ingredients_lines = []
        for ing in req.ingredients:
            qty_str = f"{ing.quantity}{ing.unit}" if ing.quantity is not None else ing.unit
            freshness_label = {
                "good": "新鮮",
                "fair": "普通",
                "poor": "要注意",
                "unknown": ""
            }.get(ing.freshness, "")
            line = f"- {ing.name}"
            if qty_str:
                line += f" {qty_str}"
            if freshness_label:
                line += f"（{freshness_label}）"
            ingredients_lines.append(line)
        ingredients_list = "\n".join(ingredients_lines)
    else:
        ingredients_list = "（食材情報なし。冷蔵庫にある一般的な食材を使って提案してください）"

    cooking_time = req.cooking_time
    if cooking_time >= 999:
        cooking_time_label = "時間無制限"
    else:
        cooking_time_label = f"{cooking_time}分以内"

    effort_label_map = {"easy": "ラクチン（切るだけ・レンチン等）", "normal": "普通（炒める・煮る等）", "hard": "本格派（じっくり丁寧に）"}
    effort_label = effort_label_map.get(req.effort_level, req.effort_level)

    mood_parts = list(req.mood_tags)
    if req.mood_freetext.strip():
        mood_parts.append(req.mood_freetext.strip())
    mood_description = "、".join(mood_parts) if mood_parts else "おまかせ"

    negative_tags = context.structured_feedback.negative_tags
    negative_tags_str = "、".join(negative_tags) if negative_tags else "なし"

    positive_tags = context.structured_feedback.positive_tags
    positive_tags_str = "、".join(positive_tags) if positive_tags else "なし"

    recent_titles = context.recent_proposal_titles
    recent_titles_str = "、".join(recent_titles) if recent_titles else "なし"

    filled = prompt_template.text.format(
        allergies=allergies_str,
        forbidden_ingredients=forbidden_str,
        kitchen_tools=tools_str,
        goal=goal_str,
        ingredients_list=ingredients_list,
        cooking_time_label=cooking_time_label,
        effort_label=effort_label,
        mood_description=mood_description,
        negative_tags=negative_tags_str,
        positive_tags=positive_tags_str,
        recent_proposal_titles=recent_titles_str,
    )
    return filled


def _parse_recipe(data: dict, index: int) -> Recipe:
    """LLMが返した辞書から Recipe を構築する。"""
    today = datetime.now().strftime("%Y%m%d")
    steps = [
        RecipeStep(step=s["step"], description=s["description"])
        for s in data.get("steps", [])
    ]
    return Recipe(
        id=data.get("id", f"recipe_{index}_{today}"),
        title=data.get("title", "（タイトル不明）"),
        emoji=data.get("emoji", "🍽️"),
        description=data.get("description", ""),
        cooking_time=int(data.get("cooking_time", 20)),
        effort_level=data.get("effort_level", "normal"),
        servings=int(data.get("servings", 2)),
        tags=data.get("tags", []),
        ingredients=data.get("ingredients", []),
        steps=steps,
        nutrition_note=data.get("nutrition_note"),
        required_tools=data.get("required_tools", []),
    )


def generate_recipes(req: SuggestRequest, context: RetrievedContext) -> tuple[List[Recipe], str]:
    """
    Gemini を呼び出して3つの候補レシピを生成する。

    Returns:
        (recipes, message): Recipe のリスト（3件）と AIからのメッセージ文字列のタプル

    Raises:
        RuntimeError: Gemini API の呼び出しに失敗した場合
        ValueError: LLM のレスポンスが不正な場合
    """
    prompt_text = _build_prompt(req, context)
    client = _get_client()
    model_name = _get_model_name()

    recipe_schema = {
        "type": "object",
        "required": ["id", "title", "emoji", "description", "cooking_time", "effort_level", "servings", "tags", "ingredients", "steps", "required_tools"],
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "emoji": {"type": "string"},
            "description": {"type": "string"},
            "cooking_time": {"type": "integer"},
            "effort_level": {"type": "string"},
            "servings": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "ingredients": {"type": "array", "items": {"type": "string"}},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["step", "description"],
                    "properties": {
                        "step": {"type": "integer"},
                        "description": {"type": "string"},
                    },
                },
            },
            "nutrition_note": {"type": "string", "nullable": True},
            "required_tools": {"type": "array", "items": {"type": "string"}},
        },
    }

    response_schema = {
        "type": "object",
        "required": ["recipes", "message"],
        "properties": {
            "recipes": {
                "type": "array",
                "items": recipe_schema,
                "minItems": 3,
                "maxItems": 3,
            },
            "message": {"type": "string"},
        },
    }

    with _tracer.start_as_current_span("llm_generate_recipes") as span:
        span.set_attribute("model_name", model_name)
        span.set_attribute("prompt_name", _PROMPT_NAME)
        retry_count = 0
        t0 = time.perf_counter()

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                ),
            )
        except genai_errors.APIError as e:
            span.set_attribute("error", True)
            span.set_attribute("error_message", str(e.message))
            span.set_attribute("retry_count", retry_count)
            raise RuntimeError(f"Gemini APIの呼び出しに失敗しました: {e.message}") from e

        latency_ms = (time.perf_counter() - t0) * 1000
        span.set_attribute("latency_ms", latency_ms)
        span.set_attribute("retry_count", retry_count)

        raw_text = response.text.strip() if response.text else ""
        if not raw_text:
            span.set_attribute("error", True)
            span.set_attribute("error_message", "empty_response")
            raise ValueError("LLMが空のレスポンスを返しました")

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            span.set_attribute("error", True)
            span.set_attribute("error_message", f"json_parse_error: {e}")
            raise ValueError(f"LLMのレスポンスをJSONとして解析できませんでした: {e}") from e

        recipes_data = data.get("recipes", [])
        recipes = [_parse_recipe(r, i) for i, r in enumerate(recipes_data)]
        message = data.get("message", "今日も美味しい食事を！")
        span.set_attribute("recipe_count", len(recipes))

    return recipes, message


# 後方互換用エイリアス（#31 Orchestrator が generate_meal_plan を呼ぶため）
def generate_meal_plan(req: SuggestRequest, context: RetrievedContext):
    """後方互換エイリアス。generate_recipes を呼び出してMealPlan形式に変換する。"""
    from ..schemas import MealItem, MealPlan
    recipes, message = generate_recipes(req, context)

    def to_meal_item(recipe: Recipe, meal_type: str) -> MealItem:
        return MealItem(
            id=recipe.id,
            meal_type=meal_type,
            title=recipe.title,
            emoji=recipe.emoji,
            description=recipe.description,
            cooking_time=recipe.cooking_time,
            effort_level=recipe.effort_level,
            servings=recipe.servings,
            tags=recipe.tags,
            ingredients=recipe.ingredients,
            steps=recipe.steps,
            nutrition_note=recipe.nutrition_note,
            required_tools=recipe.required_tools,
        )

    meal_types = ["breakfast", "lunch", "dinner"]
    items = [to_meal_item(r, meal_types[i]) for i, r in enumerate(recipes[:3])]
    while len(items) < 3:
        items.append(items[-1])

    meal_plan = MealPlan(breakfast=items[0], lunch=items[1], dinner=items[2])
    return meal_plan, message
