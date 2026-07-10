"""
Feature Tag Extractor — 「不採用（もう表示しない）」にされたレシピから、
料理名ではなく除外条件として再利用できる特徴タグ（例: 揚げ物, 豚肉, 辛い）を
LLMで抽出する（SPEC.md §5.3 / ループA ネガティブFB）。

設計方針（source_extractor.py / vision_analyzer.py と同じ Gemini 呼び出しパターンに準拠）:
- Vertex AI 経由。環境変数 GEMINI_TEXT_MODEL でモデルを切り替え可能（デフォルト: gemini-3.1-flash-lite）。
- Structured Outputs（response_schema）でJSON形式の抽出結果を強制する。
- LLM呼び出しに失敗した場合は RuntimeError / ValueError を送出し、呼び出し側
  （/api/feedback の reject ハンドラ）が fallback_tags へフォールバックする。
  → 既存の提案・フィードバックフローを壊さないことを最優先とする。
"""
from __future__ import annotations

import json
import os

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from ..prompt_loader import load_prompt

_PROMPT_NAME = "feature_tag_extraction"
_DEFAULT_MODEL = "gemini-3.1-flash-lite"
_DEFAULT_LOCATION = "global"

_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["feature_tags"],
    "properties": {
        "feature_tags": {"type": "array", "items": {"type": "string"}},
    },
}


def _get_client() -> genai.Client:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT 環境変数が設定されていません")
    location = os.getenv("GEMINI_TEXT_LOCATION", _DEFAULT_LOCATION)
    return genai.Client(vertexai=True, project=project, location=location)


def _get_model_name() -> str:
    return os.getenv("GEMINI_TEXT_MODEL", _DEFAULT_MODEL)


def _normalize_tags(raw_tags: list[str]) -> list[str]:
    """LLMが返したタグを整形する。先頭の '#' を除去し、空・重複を落とす。"""
    seen: set[str] = set()
    result: list[str] = []
    for tag in raw_tags:
        if not isinstance(tag, str):
            continue
        cleaned = tag.strip().lstrip("#").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def extract_feature_tags(
    title: str,
    ingredients: list[str],
    steps: list[str],
    existing_tags: list[str] | None = None,
) -> list[str]:
    """
    不採用にされたレシピの内容から、除外条件として使える特徴タグ（'#' 無しの素のタグ）を返す。

    Args:
        title: 料理名
        ingredients: 材料リスト（"食材 量" 形式の文字列）
        steps: 作り方の各手順（文字列）
        existing_tags: レシピに元々付いていたタグ（参考情報。任意）

    Returns:
        '#' を付けていない特徴タグの配列（例: ["揚げ物", "豚肉"]）。

    Raises:
        RuntimeError: Gemini API の呼び出し・クライアント初期化に失敗した場合
        ValueError: LLM のレスポンスが空・不正な場合
    """
    prompt_template = load_prompt(_PROMPT_NAME)
    prompt_text = prompt_template.text.format(
        title=title or "(不明)",
        ingredients="、".join(ingredients) if ingredients else "(材料情報なし)",
        steps=" / ".join(steps) if steps else "(手順情報なし)",
        existing_tags="、".join(existing_tags) if existing_tags else "(なし)",
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

    return _normalize_tags(data.get("feature_tags", []))
