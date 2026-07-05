"""
Recipe Generator Agent — Gemini を使って朝・昼・夜の3食献立を Structured Outputs で生成する。

設計方針（SPEC.md §5.2 に準拠）:
- Context Retriever Agent（#29）の出力（RetrievedContext）を受け取り、
  ユーザーのプロファイル・フィードバック履歴・冷蔵庫食材をプロンプトに注入する。
- 朝・昼・夜の3食を1回のLLM呼び出しで生成する（response_mime_type="application/json"）。
- 環境変数 GEMINI_MODEL でモデルを切り替え可能にする（デフォルト: gemini-3.1-flash-lite）。
- LLM呼び出しが失敗した場合は RuntimeError を送出し、呼び出し側でモックにフォールバックする。

層1（アレルギー・禁止食材）の扱い:
- プロンプトに「絶対に使用しない」と明記することで LLM レベルで除外を指示する。
- ただし LLM は確率的処理のため、最終的な安全チェックは Recipe Reviewer Agent（#30）が担う。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from ..prompt_loader import load_prompt
from ..schemas import MealItem, MealPlan, RecipeStep, SuggestRequest
from .context_retriever import RetrievedContext

_PROMPT_NAME = "suggest"
_DEFAULT_MODEL = "gemini-3.1-flash-lite"


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

    # アレルギー・禁止食材
    allergies = context.hard_constraints.allergies
    allergies_str = "、".join(allergies) if allergies else "なし"

    forbidden = context.hard_constraints.forbidden_ingredients
    forbidden_str = "、".join(forbidden) if forbidden else "なし"

    # 調理器具
    tools = context.hard_constraints.available_kitchen_tools
    tools_str = "、".join(tools) if tools else "包丁・フライパン・鍋（基本装備のみ）"

    # 食事目標（プロファイルから取得、コンテキストには含まれないためデフォルト）
    goal_str = "特になし（バランス良く）"

    # 冷蔵庫の食材
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

    # 調理時間ラベル
    cooking_time = req.cooking_time
    if cooking_time >= 999:
        cooking_time_label = "時間無制限"
    else:
        cooking_time_label = f"{cooking_time}分以内"

    # 手間レベルラベル
    effort_label_map = {"easy": "ラクチン（切るだけ・レンチン等）", "normal": "普通（炒める・煮る等）", "hard": "本格派（じっくり丁寧に）"}
    effort_label = effort_label_map.get(req.effort_level, req.effort_level)

    # 気分・食べたいもの
    mood_parts = list(req.mood_tags)
    if req.mood_freetext.strip():
        mood_parts.append(req.mood_freetext.strip())
    mood_description = "、".join(mood_parts) if mood_parts else "おまかせ"

    # フィードバックタグ
    negative_tags = context.structured_feedback.negative_tags
    negative_tags_str = "、".join(negative_tags) if negative_tags else "なし"

    positive_tags = context.structured_feedback.positive_tags
    positive_tags_str = "、".join(positive_tags) if positive_tags else "なし"

    # テンプレートに埋め込む
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
    )
    return filled


def _parse_meal_item(data: dict, meal_type: str) -> MealItem:
    """LLMが返した辞書から MealItem を構築する。"""
    today = datetime.now().strftime("%Y%m%d")
    steps = [
        RecipeStep(step=s["step"], description=s["description"])
        for s in data.get("steps", [])
    ]
    return MealItem(
        id=data.get("id", f"{meal_type}_{today}"),
        meal_type=meal_type,
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


def generate_meal_plan(req: SuggestRequest, context: RetrievedContext) -> tuple[MealPlan, str]:
    """
    Gemini を呼び出して朝・昼・夜の3食献立を生成する。

    Returns:
        (MealPlan, message): MealPlan と AIからのメッセージ文字列のタプル

    Raises:
        RuntimeError: Gemini API の呼び出しに失敗した場合
        ValueError: LLM のレスポンスが不正な場合
    """
    prompt_text = _build_prompt(req, context)
    client = _get_client()
    model_name = _get_model_name()

    response_schema = {
        "type": "object",
        "required": ["breakfast", "lunch", "dinner", "message"],
        "properties": {
            "breakfast": {"$ref": "#/$defs/meal_item"},
            "lunch": {"$ref": "#/$defs/meal_item"},
            "dinner": {"$ref": "#/$defs/meal_item"},
            "message": {"type": "string"},
        },
        "$defs": {
            "recipe_step": {
                "type": "object",
                "required": ["step", "description"],
                "properties": {
                    "step": {"type": "integer"},
                    "description": {"type": "string"},
                },
            },
            "meal_item": {
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
                    "steps": {"type": "array", "items": {"$ref": "#/$defs/recipe_step"}},
                    "nutrition_note": {"type": "string", "nullable": True},
                    "required_tools": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }

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
        raise RuntimeError(f"Gemini APIの呼び出しに失敗しました: {e.message}") from e

    raw_text = response.text.strip() if response.text else ""
    if not raw_text:
        raise ValueError("LLMが空のレスポンスを返しました")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLMのレスポンスをJSONとして解析できませんでした: {e}") from e

    # 各食事を解析
    breakfast = _parse_meal_item(data.get("breakfast", {}), "breakfast")
    lunch = _parse_meal_item(data.get("lunch", {}), "lunch")
    dinner = _parse_meal_item(data.get("dinner", {}), "dinner")

    meal_plan = MealPlan(breakfast=breakfast, lunch=lunch, dinner=dinner)
    message = data.get("message", "今日も美味しい1日を！")

    return meal_plan, message
