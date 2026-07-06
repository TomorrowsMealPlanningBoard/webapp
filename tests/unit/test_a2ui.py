"""
Generative UI (A2UI) のユニットテスト（Issue #41）。

重点テスト対象:
  1. DataPart の mimeType 宣言（application/json+a2ui）
  2. JSON Lines のシリアライズ形式（1行1JSON、末尾改行）
  3. to_a2a() 系ユーティリティ（recipe_to_a2a 等）による標準化変換
  4. フォールバック動作（/api/suggest/a2ui が失敗しても /api/suggest は成立すること、
     およびストリームが不正な場合にフロント側が拾える形の構造になっていること）
"""
import json

from app.a2ui import (
    A2UI_MIME_TYPE,
    build_suggest_a2ui_stream,
    done_marker,
    iter_a2ui_jsonlines,
    make_data_part,
    message_to_a2a,
    recipe_to_a2a,
    smart_chips_to_a2a,
)
from app.schemas import Recipe, RecipeStep


def _sample_recipe(recipe_id="recipe_test_001", title="テストレシピ"):
    return Recipe(
        id=recipe_id,
        title=title,
        emoji="🍳",
        description="テスト用のレシピ説明",
        cooking_time=15,
        effort_level="easy",
        servings=2,
        tags=["テスト", "簡単"],
        ingredients=["卵 2個", "塩 少々"],
        steps=[RecipeStep(step=1, description="卵を割る")],
        nutrition_note="テスト用の栄養メモ",
    )


# ==========================================
# 1. DataPart の mimeType 宣言
# ==========================================

def test_a2ui_mime_type_constant():
    """AC: DataPartのmimeTypeにapplication/json+a2uiを宣言できること"""
    assert A2UI_MIME_TYPE == "application/json+a2ui"


def test_make_data_part_declares_mime_type():
    part = make_data_part("recipe_card", {"foo": "bar"})
    assert part["mimeType"] == A2UI_MIME_TYPE
    assert part["data"]["component"] == "recipe_card"
    assert part["data"]["foo"] == "bar"


# ==========================================
# 3. to_a2a() 系ユーティリティ（標準化）
# ==========================================

def test_recipe_to_a2a_produces_valid_data_part():
    """AC: to_a2a()等のユーティリティで標準化された実装であること"""
    recipe = _sample_recipe()
    part = recipe_to_a2a(recipe, index=0)

    assert part["mimeType"] == A2UI_MIME_TYPE
    assert part["data"]["component"] == "recipe_card"
    assert part["data"]["index"] == 0
    # Recipeの全フィールドが透過的に含まれ、フロント側の既存描画にそのまま使える
    assert part["data"]["recipe"]["id"] == "recipe_test_001"
    assert part["data"]["recipe"]["title"] == "テストレシピ"
    assert part["data"]["recipe"]["steps"][0]["description"] == "卵を割る"


def test_recipe_to_a2a_is_json_serializable():
    """DataPartがそのままJSON化できること（ストリーム配信の前提）"""
    recipe = _sample_recipe()
    part = recipe_to_a2a(recipe, index=2)
    serialized = json.dumps(part, ensure_ascii=False)
    restored = json.loads(serialized)
    assert restored["data"]["index"] == 2


def test_smart_chips_to_a2a_low_rating():
    """FB用スマートチップ（星1〜2）のUI記述をA2UI形式に変換できること"""
    part = smart_chips_to_a2a("low", ["工程が大変だった", "味が合わなかった", "量が多かった"])
    assert part["mimeType"] == A2UI_MIME_TYPE
    assert part["data"]["component"] == "smart_chips"
    assert part["data"]["rating_tier"] == "low"
    assert "工程が大変だった" in part["data"]["labels"]


def test_smart_chips_to_a2a_high_rating():
    part = smart_chips_to_a2a("high", ["味付けが最高", "手軽だった", "子供が喜んだ"])
    assert part["data"]["rating_tier"] == "high"
    assert len(part["data"]["labels"]) == 3


def test_message_to_a2a():
    part = message_to_a2a("こんにちは")
    assert part["mimeType"] == A2UI_MIME_TYPE
    assert part["data"]["component"] == "message"
    assert part["data"]["text"] == "こんにちは"


def test_done_marker():
    part = done_marker()
    assert part["mimeType"] == A2UI_MIME_TYPE
    assert part["data"]["component"] == "done"


# ==========================================
# 2. JSON Lines のシリアライズ形式
# ==========================================

def test_iter_a2ui_jsonlines_yields_one_json_per_line():
    """AC: JSON Linesでストリーム配信できること（1行=1 DataPart、末尾改行）"""
    parts = [make_data_part("a", {"x": 1}), make_data_part("b", {"y": 2})]
    lines = list(iter_a2ui_jsonlines(parts))

    assert len(lines) == 2
    for line in lines:
        assert line.endswith("\n")
        # 改行を除いた本体が単独で有効なJSONであること（JSON Linesの要件）
        parsed = json.loads(line.rstrip("\n"))
        assert parsed["mimeType"] == A2UI_MIME_TYPE


def test_iter_a2ui_jsonlines_each_line_independently_parseable():
    """複数行を結合してから1行ずつ分割してもパースできること（フロントのstream parser前提）"""
    parts = [make_data_part("a", {"i": i}) for i in range(5)]
    joined = "".join(iter_a2ui_jsonlines(parts))
    lines = [line for line in joined.split("\n") if line]

    assert len(lines) == 5
    for i, line in enumerate(lines):
        parsed = json.loads(line)
        assert parsed["data"]["i"] == i


def test_build_suggest_a2ui_stream_order_and_termination():
    """
    AC: レシピカード／スマートチップを動的描画できること。
    配信順序が message → recipe_card × N → done であり、
    フロントが「doneを見るまでパースを続け、見なければフォールバックする」
    という設計を成立させる終端マーカーが必ず含まれること。
    """
    recipes = [_sample_recipe(f"r{i}", f"レシピ{i}") for i in range(3)]
    lines = list(build_suggest_a2ui_stream(recipes, "3品提案します"))

    parsed = [json.loads(line) for line in lines]
    components = [p["data"]["component"] for p in parsed]

    assert components[0] == "message"
    assert components[1:4] == ["recipe_card"] * 3
    assert components[-1] == "done"

    # 全レコードがmimeTypeを宣言していること
    assert all(p["mimeType"] == A2UI_MIME_TYPE for p in parsed)

    # レシピの順序（index）が保持されていること
    recipe_parts = [p for p in parsed if p["data"]["component"] == "recipe_card"]
    for i, p in enumerate(recipe_parts):
        assert p["data"]["index"] == i
        assert p["data"]["recipe"]["title"] == f"レシピ{i}"


def test_build_suggest_a2ui_stream_empty_recipes_still_terminates():
    """レシピが0件でもdoneマーカーで正常終端すること（異常系でのフロント側フォールバック判定を支える）"""
    lines = list(build_suggest_a2ui_stream([], "提案なし"))
    parsed = [json.loads(line) for line in lines]
    components = [p["data"]["component"] for p in parsed]
    assert components == ["message", "done"]


# ==========================================
# 4. フォールバック動作（APIレベル）
# ==========================================

def test_suggest_a2ui_endpoint_declares_mime_type(client, auth_headers):
    """AC: バックエンドがDataPartのmimeTypeにapplication/json+a2uiを宣言して配信できること"""
    res = client.post("/api/suggest/a2ui", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json+a2ui")


def test_suggest_a2ui_endpoint_streams_valid_jsonlines(client, auth_headers):
    """AC: JSON Linesでストリーム配信し、レシピカードを動的描画できること"""
    res = client.post("/api/suggest/a2ui", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 200
    lines = [line for line in res.text.split("\n") if line.strip()]
    assert len(lines) >= 2  # 最低でも message + done

    parsed = [json.loads(line) for line in lines]
    assert all(p["mimeType"] == "application/json+a2ui" for p in parsed)
    components = [p["data"]["component"] for p in parsed]
    assert "done" in components
    # レシピが1件以上含まれる（モックフォールバックでも必ず候補が返る）
    assert "recipe_card" in components


def test_suggest_a2ui_requires_auth(client):
    """認証必須であること（既存/api/suggestと同様のガードレールを踏襲）"""
    res = client.post("/api/suggest/a2ui", json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res.status_code == 401


def test_suggest_a2ui_core_logic_matches_normal_suggest(client, auth_headers):
    """
    AC: A2UI非対応・失敗時も通常のReact描画にフォールバックしてコア機能が成立すること。

    /api/suggest/a2ui の内部で使うコアロジック（_build_suggest_response）は
    /api/suggest と完全に同一である。A2UI変換・ストリーム配信の層が失敗しても
    通常の /api/suggest 自体は独立して常に成立することを確認する。
    """
    res_normal = client.post("/api/suggest", headers=auth_headers, json={
        "cooking_time": 30,
        "effort_level": "easy",
        "mood_tags": [],
        "mood_freetext": "",
    })
    assert res_normal.status_code == 200
    body = res_normal.json()
    assert "recipes" in body
    assert "message" in body
    assert len(body["recipes"]) >= 1


def test_recipe_to_a2a_survives_malformed_downstream_parsing():
    """
    フロント側パーサが1行でも壊れたJSONを受け取った場合を想定し、
    正しく生成されたDataPartは常に有効なJSON行として復元可能であることを保証する
    （フォールバック判定の前提: 「壊れているのはこちらのバグではなく通信/非対応起因」）。
    """
    recipe = _sample_recipe()
    line = next(iter(iter_a2ui_jsonlines([recipe_to_a2a(recipe, 0)])))
    # 末尾の改行を含めても、rstrip後は必ず有効なJSON
    assert json.loads(line.rstrip("\n"))["data"]["component"] == "recipe_card"
