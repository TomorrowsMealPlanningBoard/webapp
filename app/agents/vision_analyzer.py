"""
Vision Analyzer Agent — Gemini Vision を使って冷蔵庫画像から食材リストを構造化抽出する。
"""
from __future__ import annotations

import os
from typing import Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from ..prompt_loader import load_prompt


class Ingredient(BaseModel):
    name: str
    quantity: Optional[float] = None
    unit: str = ""
    freshness: str = "unknown"  # good / fair / poor / unknown


class VisionAnalysisResult(BaseModel):
    ingredients: list[Ingredient]
    prompt_version: str = "unknown"  # 使用したプロンプトファイルのGitコミットハッシュ（提案ログ用）


_PROMPT_NAME = "vision_analysis"


def _get_client() -> genai.Client:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT 環境変数が設定されていません")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    return genai.Client(vertexai=True, project=project, location=location)


def analyze_image(image_bytes: bytes, mime_type: str) -> VisionAnalysisResult:
    """
    冷蔵庫画像を受け取り、食材リストを返す。
    画像が空・認識不能な場合は ValueError を送出する。
    """
    if not image_bytes:
        raise ValueError("画像データが空です")

    prompt = load_prompt(_PROMPT_NAME)
    client = _get_client()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=[prompt.text, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
    except genai_errors.APIError as e:
        raise RuntimeError(f"Gemini APIの呼び出しに失敗しました: {e.message}") from e

    raw_text = response.text.strip() if response.text else ""
    if not raw_text:
        raise ValueError("AIが画像を認識できませんでした")

    result = VisionAnalysisResult.model_validate_json(raw_text)
    result.prompt_version = prompt.version

    if not result.ingredients:
        raise ValueError("画像から食材を認識できませんでした")

    return result
