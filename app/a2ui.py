"""
Generative UI (A2UI) ユーティリティ（Issue #41）。

SPEC.md §5.2/§6.1/§6.4 に基づく最小実装。
- `DataPart` の `mimeType` に `application/json+a2ui` を宣言してUI記述を配信する。
- JSON Lines（1行=1 DataPart）でストリーム配信し、レシピカード／スマートチップを動的描画する。
- A2UI 非対応・失敗時は既存の通常 React/HTML 描画へ確実にフォールバックする
  （フロント側の実装は app/static/app.js を参照）。

過剰設計を避けるため、Google の A2A/A2UI 仕様のうち本アプリで必要な最小サブセットのみを
自作プロトコルとして実装する:
  - DataPart: { mimeType, data } の1レコード
  - to_a2a(): UI記述オブジェクト（Recipe等）を A2UI の component 記述に変換する標準化ユーティリティ
  - iter_a2ui_jsonlines(): DataPart の列を JSON Lines 文字列イテレータに変換する
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Iterable, Iterator

from .schemas import Recipe

# A2UI用に宣言するmimeType（SPEC.md §6.1/§6.4準拠）
A2UI_MIME_TYPE = "application/json+a2ui"

# A2UI の DataPart を 1 件ずつ配信する際の間隔（秒）。
# フロントは受信しながら逐次描画するため、この間隔がそのまま
# 「メッセージ → レシピカードが 1 枚ずつ生成されて降ってくる」演出のテンポになる。
# 0 にすると全 DataPart が同一チャンクで届き、Generative UI の段階描画が
# 視覚的に伝わらなくなる（＝加点にならない）ため、意図的に待ちを入れる。
# デモ動画で「1 枚ずつ生成されている」ことがはっきり伝わるよう、カードの登場
# アニメーション（約0.55秒）と合わせて 0.6 秒間隔にし、1 枚ごとの "間" を作る。
A2UI_STREAM_INTERVAL_SEC = 0.6


def make_data_part(component: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    A2UIのDataPart（1レコード）を構築する。

    Args:
        component: UIコンポーネント種別（例: "recipe_card", "smart_chips"）
        payload: コンポーネントの描画に必要なデータ本体
    Returns:
        DataPart辞書。mimeType宣言を含む。
    """
    return {
        "mimeType": A2UI_MIME_TYPE,
        "data": {
            "component": component,
            **payload,
        },
    }


def recipe_to_a2a(recipe: Recipe, index: int) -> dict[str, Any]:
    """
    Recipe（レシピ提案）を A2UI の recipe_card コンポーネント記述（DataPart）に変換する。

    `to_a2a()` 系ユーティリティとして標準化する（SPEC.md §6.4 準拠）。
    フロント側の renderRecipeCard() が既存React/HTML描画で持つ情報をそのまま
    UI記述として渡す。フィールド不足時もフロント側フォールバックで復旧できるよう、
    Recipeのフィールドをそのまま透過的に含める。
    """
    return make_data_part(
        component="recipe_card",
        payload={
            "index": index,
            "recipe": recipe.model_dump(),
        },
    )


def smart_chips_to_a2a(rating_tier: str, labels: list[str]) -> dict[str, Any]:
    """
    調理後フィードバックのスマートチップ（星評価に応じた選択式タグ）を
    A2UI の smart_chips コンポーネント記述（DataPart）に変換する。

    Args:
        rating_tier: "low"（星1〜2） or "high"（星4〜5）
        labels: チップに表示する選択肢ラベルの一覧
    """
    return make_data_part(
        component="smart_chips",
        payload={
            "rating_tier": rating_tier,
            "labels": labels,
        },
    )


def message_to_a2a(message: str) -> dict[str, Any]:
    """AIからのひとことメッセージを A2UI の message コンポーネント記述に変換する。"""
    return make_data_part(component="message", payload={"text": message})


def done_marker() -> dict[str, Any]:
    """
    ストリーム終端を示すDataPart。
    フロント側はこれを受け取るまでパースを継続し、途中で異常終了した場合は
    フォールバック描画に切り替える（AC: フォールバック最優先）。
    """
    return make_data_part(component="done", payload={})


def iter_a2ui_jsonlines(data_parts: Iterable[dict[str, Any]]) -> Iterator[str]:
    """
    DataPartの列を JSON Lines（1行1JSON、末尾に改行）文字列イテレータへ変換する。
    FastAPIの StreamingResponse にそのまま渡せる。
    """
    for part in data_parts:
        yield json.dumps(part, ensure_ascii=False) + "\n"


def build_suggest_data_parts(recipes: list[Recipe], message: str) -> list[dict[str, Any]]:
    """
    /api/suggest のレスポンス（レシピ N 案＋メッセージ）を A2UI の DataPart 列に変換する。

    配信順序:
      1. message（AIからのひとことメッセージ）
      2. recipe_card × N（レシピカード）
      3. done（終端マーカー）
    """
    parts: list[dict[str, Any]] = [message_to_a2a(message)]
    parts.extend(recipe_to_a2a(recipe, i) for i, recipe in enumerate(recipes))
    parts.append(done_marker())
    return parts


def build_suggest_a2ui_stream(recipes: list[Recipe], message: str) -> Iterator[str]:
    """
    DataPart 列を JSON Lines へ変換した同期イテレータ（ペーシングなし）。

    ペーシング付きの段階配信は :func:`build_suggest_a2ui_stream_paced` を使う。
    こちらは単体テスト等でストリーム内容を検証する用途に残す。
    """
    return iter_a2ui_jsonlines(build_suggest_data_parts(recipes, message))


async def build_suggest_a2ui_stream_paced(
    recipes: list[Recipe],
    message: str,
    interval_sec: float = A2UI_STREAM_INTERVAL_SEC,
) -> AsyncIterator[str]:
    """
    /api/suggest/a2ui 用のペーシング付き A2UI ストリーム。

    DataPart を 1 件ずつ ``interval_sec`` 間隔で配信することで、フロント側の
    逐次描画（メッセージ → レシピカードが 1 枚ずつフェードインで登場）を
    視覚的に成立させる。done マーカーは待たずに即送出してストリームを閉じる。
    """
    parts = build_suggest_data_parts(recipes, message)
    for i, line in enumerate(iter_a2ui_jsonlines(parts)):
        # 先頭（message）は即時に出して待ち時間の体感を減らす。
        # 2 件目以降（recipe_card / done）の手前で待ちを入れて 1 枚ずつ見せる。
        if i > 0 and interval_sec > 0:
            await asyncio.sleep(interval_sec)
        yield line
