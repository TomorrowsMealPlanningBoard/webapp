"""
RecipeGeneratorAgent のユニットテスト。
Gemini API 呼び出し部分はモックで差し替える。
"""
from unittest.mock import MagicMock, patch

import pytest

from app.agents.context_retriever import (
    FavoriteRecipeSource,
    HardConstraints,
    RetrievedContext,
    StructuredFeedbackContext,
)
from app.agents.recipe_generator import _build_prompt, generate_recipes
from app.schemas import IngredientItem, SuggestRequest


# テスト用のコンテキスト（アレルギー・禁止食材あり）
@pytest.fixture
def sample_request():
    return SuggestRequest(
        cooking_time=30,
        effort_level="normal",
        mood_tags=["肉料理", "さっぱり"],
        mood_freetext="疲れているので消化が良いもの",
        ingredients=[
            IngredientItem(name="鶏むね肉", quantity=200, unit="g", freshness="good"),
            IngredientItem(name="キャベツ", quantity=None, unit="", freshness="unknown"),
        ],
    )


@pytest.fixture
def sample_context():
    return RetrievedContext(
        user_id="test-user-001",
        hard_constraints=HardConstraints(
            allergies=["卵", "牛乳"],
            forbidden_ingredients=["セロリ"],
            available_kitchen_tools=["frying_pan_large", "knife_board"],
        ),
        structured_feedback=StructuredFeedbackContext(
            negative_tags=["#揚げ物"],
            positive_tags=["#さっぱり"],
        ),
        similar_snippets=[],
    )


# --- プロンプト構築のテスト ---

def test_build_prompt_includes_allergies(sample_request, sample_context):
    """プロンプトにアレルギー情報が含まれること"""
    prompt = _build_prompt(sample_request, sample_context)
    assert "卵" in prompt
    assert "牛乳" in prompt


def test_build_prompt_includes_forbidden_ingredients(sample_request, sample_context):
    """プロンプトに禁止食材が含まれること"""
    prompt = _build_prompt(sample_request, sample_context)
    assert "セロリ" in prompt


def test_build_prompt_includes_ingredients_from_request(sample_request, sample_context):
    """プロンプトに冷蔵庫の食材が含まれること"""
    prompt = _build_prompt(sample_request, sample_context)
    assert "鶏むね肉" in prompt
    assert "キャベツ" in prompt


def test_build_prompt_includes_mood_tags(sample_request, sample_context):
    """プロンプトに気分タグが含まれること"""
    prompt = _build_prompt(sample_request, sample_context)
    assert "肉料理" in prompt or "さっぱり" in prompt


def test_build_prompt_includes_negative_tags(sample_request, sample_context):
    """プロンプトに不採用タグが含まれること"""
    prompt = _build_prompt(sample_request, sample_context)
    assert "揚げ物" in prompt


def test_build_prompt_no_ingredients_shows_fallback(sample_context):
    """食材リストが空の場合はフォールバックメッセージが含まれること"""
    req = SuggestRequest(
        cooking_time=30,
        effort_level="normal",
        mood_tags=[],
        mood_freetext="",
        ingredients=[],
    )
    prompt = _build_prompt(req, sample_context)
    assert "食材情報なし" in prompt


def test_build_prompt_unlimited_cooking_time(sample_context):
    """調理時間が999（無制限）の場合はラベルが変わること"""
    req = SuggestRequest(
        cooking_time=999,
        effort_level="normal",
        mood_tags=[],
        mood_freetext="",
    )
    prompt = _build_prompt(req, sample_context)
    assert "時間無制限" in prompt


def test_build_prompt_no_favorite_sources_shows_fallback(sample_request, sample_context):
    """お気に入りレシピソースが未登録の場合はフォールバック表記になること"""
    prompt = _build_prompt(sample_request, sample_context)
    assert "登録なし" in prompt


def test_build_prompt_includes_all_favorite_recipe_sources(sample_request, sample_context):
    """
    Issue #78: 登録された外部レシピソースが全件そのままプロンプトへ直接注入されること
    （ベクトル検索・上位N件抽出は行わない）。
    """
    sample_context.favorite_recipe_sources = [
        FavoriteRecipeSource(
            seasoning_tendency="醤油とみりんベースの甘辛い味付けを好む",
            favorite_ingredient_combos=["豚肉と玉ねぎ"],
            cooking_style="短時間で作れる炒め物中心",
            tags=["和食", "時短"],
            source_title="甘辛い豚丼の作り方",
            source_url="https://example.com/video1",
        ),
        FavoriteRecipeSource(
            seasoning_tendency="塩味を活かしたシンプルな味付け",
            cooking_style="オーブンを多用する",
            source_title="塩焼き特集",
            source_url="https://example.com/video2",
        ),
    ]
    prompt = _build_prompt(sample_request, sample_context)
    assert "甘辛い豚丼の作り方" in prompt
    assert "醤油とみりんベースの甘辛い味付けを好む" in prompt
    assert "豚肉と玉ねぎ" in prompt
    assert "塩焼き特集" in prompt
    assert "塩味を活かしたシンプルな味付け" in prompt


# --- LLM 呼び出しのモックテスト ---

def _make_recipe_json(recipe_id: str, title: str, emoji: str = "🍳",
                      cooking_time: int = 20, effort_level: str = "normal",
                      servings: int = 2, tags=None, ingredients=None,
                      steps=None, nutrition_note=None) -> dict:
    return {
        "id": recipe_id,
        "title": title,
        "emoji": emoji,
        "description": f"{title}の説明",
        "cooking_time": cooking_time,
        "effort_level": effort_level,
        "servings": servings,
        "tags": tags or ["和食"],
        "ingredients": ingredients or ["食材A 適量"],
        "steps": steps or [{"step": 1, "description": "作る"}],
        "nutrition_note": nutrition_note,
        "required_tools": [],
    }


import json

_VALID_LLM_RESPONSE = json.dumps({
    "recipes": [
        _make_recipe_json("recipe_001", "鶏むね肉のさっぱり炒め", "🍳",
                          cooking_time=25, effort_level="normal", servings=2,
                          tags=["さっぱり", "肉料理"],
                          ingredients=["鶏むね肉 200g", "キャベツ 1/4玉"],
                          steps=[{"step": 1, "description": "鶏肉を切る"},
                                 {"step": 2, "description": "キャベツと炒める"}]),
        _make_recipe_json("recipe_002", "キャベツの味噌汁", "🍲",
                          cooking_time=15, effort_level="easy", servings=2,
                          tags=["汁物", "和食"],
                          ingredients=["キャベツ 1/4玉", "味噌 大さじ2"],
                          steps=[{"step": 1, "description": "だしを取る"},
                                 {"step": 2, "description": "野菜を入れて煮る"}]),
        _make_recipe_json("recipe_003", "サラダチキン", "🥗",
                          cooking_time=20, effort_level="easy", servings=1,
                          tags=["ヘルシー", "鶏肉"],
                          ingredients=["鶏むね肉 150g", "レタス 適量"],
                          steps=[{"step": 1, "description": "鶏肉を茹でる"},
                                 {"step": 2, "description": "サラダに盛り付ける"}]),
    ],
    "message": "今日も美味しい食事を！",
})


def test_generate_recipes_success(sample_request, sample_context, monkeypatch):
    """LLMが正常なJSONを返した場合に3つのRecipeリストが返ること"""
    mock_response = MagicMock()
    mock_response.text = _VALID_LLM_RESPONSE

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        recipes, message = generate_recipes(sample_request, sample_context)

    assert len(recipes) == 3
    assert recipes[0].title == "鶏むね肉のさっぱり炒め"
    assert recipes[1].title == "キャベツの味噌汁"
    assert recipes[2].title == "サラダチキン"
    assert message == "今日も美味しい食事を！"


def test_generate_recipes_raises_on_api_error(sample_request, sample_context):
    """APIエラー時にRuntimeErrorを送出すること"""
    from google.genai import errors as genai_errors

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = genai_errors.APIError(
        code=500, response_json={"error": {"message": "Internal error"}}
    )

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Gemini API"):
            generate_recipes(sample_request, sample_context)


def test_generate_recipes_raises_on_empty_response(sample_request, sample_context):
    """LLMが空のレスポンスを返した場合にValueErrorを送出すること"""
    mock_response = MagicMock()
    mock_response.text = ""

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        with pytest.raises(ValueError, match="空のレスポンス"):
            generate_recipes(sample_request, sample_context)


def test_generate_recipes_raises_on_invalid_json(sample_request, sample_context):
    """LLMが不正なJSONを返した場合にValueErrorを送出すること"""
    mock_response = MagicMock()
    mock_response.text = "これはJSONではありません"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        with pytest.raises(ValueError, match="JSON"):
            generate_recipes(sample_request, sample_context)


def test_generate_recipes_uses_gemini_model_env(sample_request, sample_context, monkeypatch):
    """GEMINI_MODEL環境変数が使われること"""
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

    mock_response = MagicMock()
    mock_response.text = _VALID_LLM_RESPONSE

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        generate_recipes(sample_request, sample_context)

    call_kwargs = mock_client.models.generate_content.call_args
    assert call_kwargs[1]["model"] == "gemini-3.1-flash-lite"


def test_suggest_endpoint_falls_back_to_mock_on_llm_failure(client, auth_headers):
    """
    LLM呼び出しが失敗した場合にモックデータがフォールバックとして返ること。
    エラーが出ても /api/suggest は 200 を返す必要がある。
    """
    with patch("app.agents.recipe_generator.generate_recipes",
               side_effect=RuntimeError("Gemini APIエラー")):
        res = client.post("/api/suggest", headers=auth_headers, json={
            "cooking_time": 30,
            "effort_level": "normal",
            "mood_tags": [],
            "mood_freetext": "",
        })

    assert res.status_code == 200
    body = res.json()
    assert "recipes" in body
    assert len(body["recipes"]) > 0
    assert "message" in body
