"""
Source Extractor — スクレイピングした外部レシピソース（YouTube/ブログ）から
「味付けの傾向」「好まれる食材の組み合わせ」「調理スタイル」をLLMで抽出する（Issue #32）。

設計方針（SPEC.md §5.4 / recipe_generator.py と同じ Gemini 呼び出しパターンに準拠）:
- 環境変数 GEMINI_MODEL でモデルを切り替え可能にする（デフォルト: gemini-3.1-flash-lite）。
- Structured Outputs（response_schema）でJSON形式の抽出結果を強制する。
- LLM呼び出し失敗時は RuntimeError を送出し、呼び出し側（/api/sources）で
  エラーレスポンスに変換する（既存の提案フローには影響させない）。
"""
from __future__ import annotations

import json
import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field

from ..prompt_loader import load_prompt
from .source_scraper import ScrapedSource

_PROMPT_NAME = "source_extraction"
_DEFAULT_MODEL = "gemini-3.1-flash-lite"


class ExtractedSourceProfile(BaseModel):
    """LLMによる抽出結果。層3ナレッジストアへ保存する構造化サマリ。"""

    seasoning_tendency: str = ""
    favorite_ingredient_combos: list[str] = Field(default_factory=list)
    cooking_style: str = ""
    tags: list[str] = Field(default_factory=list)

    def to_snippet_text(self, title: str) -> str:
        """RecipeSnippet.text として使うRAG検索用テキストに変換する。"""
        parts = [f"「{title}」から抽出した好みの傾向。"]
        if self.seasoning_tendency:
            parts.append(f"味付けの傾向: {self.seasoning_tendency}")
        if self.favorite_ingredient_combos:
            parts.append(f"好まれる食材の組み合わせ: {'、'.join(self.favorite_ingredient_combos)}")
        if self.cooking_style:
            parts.append(f"調理スタイル: {self.cooking_style}")
        return " ".join(parts)


def _get_client() -> genai.Client:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT 環境変数が設定されていません")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    return genai.Client(vertexai=True, project=project, location=location)


def _get_model_name() -> str:
    return os.getenv("GEMINI_MODEL", _DEFAULT_MODEL)


_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["seasoning_tendency", "favorite_ingredient_combos", "cooking_style", "tags"],
    "properties": {
        "seasoning_tendency": {"type": "string"},
        "favorite_ingredient_combos": {"type": "array", "items": {"type": "string"}},
        "cooking_style": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
}


def extract_profile(scraped: ScrapedSource) -> ExtractedSourceProfile:
    """
    スクレイピング結果から「味付けの傾向」「好まれる食材の組み合わせ」「調理スタイル」を
    LLMで抽出する。

    Raises:
        RuntimeError: Gemini API の呼び出しに失敗した場合
        ValueError: LLM のレスポンスが不正な場合
    """
    prompt_template = load_prompt(_PROMPT_NAME)
    prompt_text = prompt_template.text.format(
        title=scraped.title,
        text_content=scraped.text_content or "(本文なし)",
    )

    client = _get_client()
    model_name = _get_model_name()

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt_text,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
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

    return ExtractedSourceProfile(
        seasoning_tendency=data.get("seasoning_tendency", ""),
        favorite_ingredient_combos=data.get("favorite_ingredient_combos", []),
        cooking_style=data.get("cooking_style", ""),
        tags=data.get("tags", []),
    )
