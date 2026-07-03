"""
Vision Analyzer Agent — Gemini Vision を使って冷蔵庫画像から食材リストを構造化抽出する。
"""
from __future__ import annotations

import os
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel


class Ingredient(BaseModel):
    name: str
    quantity: Optional[float] = None
    unit: str = ""
    freshness: str = "unknown"  # good / fair / poor / unknown


class VisionAnalysisResult(BaseModel):
    ingredients: list[Ingredient]


_SYSTEM_PROMPT = """
あなたは冷蔵庫の写真を分析する食材認識AIです。
画像に写っている食材をすべて特定し、以下のJSON形式で返してください。

出力フォーマット（JSONのみ。説明文は不要）:
{
  "ingredients": [
    {"name": "食材名", "quantity": 数値または null, "unit": "個/本/ml/g など", "freshness": "good/fair/poor/unknown"},
    ...
  ]
}

ルール:
- 画像に食材が認識できない、または画像が不明瞭な場合は {"ingredients": []} を返す
- アレルギー食材も除外せずすべて含める
- quantity が判断できない場合は null にする
- freshness は見た目から判断できる場合のみ good/fair/poor を使用し、不明の場合は unknown にする
"""


def _get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY または GOOGLE_API_KEY 環境変数が設定されていません")
    return genai.Client(api_key=api_key)


def analyze_image(image_bytes: bytes, mime_type: str) -> VisionAnalysisResult:
    """
    冷蔵庫画像を受け取り、食材リストを返す。
    画像が空・認識不能な場合は ValueError を送出する。
    """
    if not image_bytes:
        raise ValueError("画像データが空です")

    client = _get_client()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=[_SYSTEM_PROMPT, image_part],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )

    raw_text = response.text.strip() if response.text else ""
    if not raw_text:
        raise ValueError("AIが画像を認識できませんでした")

    result = VisionAnalysisResult.model_validate_json(raw_text)

    if not result.ingredients:
        raise ValueError("画像から食材を認識できませんでした")

    return result
