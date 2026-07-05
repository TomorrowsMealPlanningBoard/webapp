"""
RecipeGeneratorAgent のユニットテスト。
Gemini API 呼び出し部分はモックで差し替える。
"""
import pytest
from unittest.mock import MagicMock, patch
from app.agents.recipe_generator import generate_meal_plan, _build_prompt
from app.agents.context_retriever import (
    RetrievedContext,
    HardConstraints,
    StructuredFeedbackContext,
)
from app.schemas import SuggestRequest, IngredientItem


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


# --- LLM 呼び出しのモックテスト ---

_VALID_LLM_RESPONSE = """{
    "breakfast": {
        "id": "breakfast_20260705",
        "meal_type": "breakfast",
        "title": "トースト&スクランブルエッグ",
        "emoji": "🍳",
        "description": "シンプルで素早く作れる朝食",
        "cooking_time": 10,
        "effort_level": "easy",
        "servings": 1,
        "tags": ["朝食", "簡単"],
        "ingredients": ["食パン 2枚", "バター 少々"],
        "steps": [
            {"step": 1, "description": "食パンをトーストする"},
            {"step": 2, "description": "バターを塗って完成"}
        ],
        "nutrition_note": null,
        "required_tools": []
    },
    "lunch": {
        "id": "lunch_20260705",
        "meal_type": "lunch",
        "title": "鶏むね肉のサラダ",
        "emoji": "🥗",
        "description": "ヘルシーなランチ",
        "cooking_time": 20,
        "effort_level": "normal",
        "servings": 1,
        "tags": ["ヘルシー", "鶏肉"],
        "ingredients": ["鶏むね肉 150g", "レタス 適量"],
        "steps": [
            {"step": 1, "description": "鶏肉を茹でる"},
            {"step": 2, "description": "サラダに盛り付ける"}
        ],
        "nutrition_note": "高タンパク",
        "required_tools": ["pot_single"]
    },
    "dinner": {
        "id": "dinner_20260705",
        "meal_type": "dinner",
        "title": "キャベツと鶏むね肉のさっぱり炒め",
        "emoji": "🍽️",
        "description": "さっぱりした夕食",
        "cooking_time": 25,
        "effort_level": "normal",
        "servings": 2,
        "tags": ["さっぱり", "肉料理"],
        "ingredients": ["鶏むね肉 200g", "キャベツ 1/4玉"],
        "steps": [
            {"step": 1, "description": "鶏肉を切る"},
            {"step": 2, "description": "キャベツと炒める"}
        ],
        "nutrition_note": null,
        "required_tools": ["frying_pan_large"]
    },
    "message": "今日も美味しい1日を！"
}"""


def test_generate_meal_plan_success(sample_request, sample_context, monkeypatch):
    """LLMが正常なJSONを返した場合にMealPlanが返ること"""
    mock_response = MagicMock()
    mock_response.text = _VALID_LLM_RESPONSE

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        meal_plan, message = generate_meal_plan(sample_request, sample_context)

    assert meal_plan.breakfast.title == "トースト&スクランブルエッグ"
    assert meal_plan.breakfast.meal_type == "breakfast"
    assert meal_plan.lunch.title == "鶏むね肉のサラダ"
    assert meal_plan.lunch.meal_type == "lunch"
    assert meal_plan.dinner.title == "キャベツと鶏むね肉のさっぱり炒め"
    assert meal_plan.dinner.meal_type == "dinner"
    assert message == "今日も美味しい1日を！"


def test_generate_meal_plan_raises_on_api_error(sample_request, sample_context):
    """APIエラー時にRuntimeErrorを送出すること"""
    from google.genai import errors as genai_errors

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = genai_errors.APIError(
        code=500, response_json={"error": {"message": "Internal error"}}
    )

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Gemini API"):
            generate_meal_plan(sample_request, sample_context)


def test_generate_meal_plan_raises_on_empty_response(sample_request, sample_context):
    """LLMが空のレスポンスを返した場合にValueErrorを送出すること"""
    mock_response = MagicMock()
    mock_response.text = ""

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        with pytest.raises(ValueError, match="空のレスポンス"):
            generate_meal_plan(sample_request, sample_context)


def test_generate_meal_plan_raises_on_invalid_json(sample_request, sample_context):
    """LLMが不正なJSONを返した場合にValueErrorを送出すること"""
    mock_response = MagicMock()
    mock_response.text = "これはJSONではありません"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        with pytest.raises(ValueError, match="JSON"):
            generate_meal_plan(sample_request, sample_context)


def test_generate_meal_plan_uses_gemini_model_env(sample_request, sample_context, monkeypatch):
    """GEMINI_MODEL環境変数が使われること"""
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")

    mock_response = MagicMock()
    mock_response.text = _VALID_LLM_RESPONSE

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.recipe_generator._get_client", return_value=mock_client):
        generate_meal_plan(sample_request, sample_context)

    call_kwargs = mock_client.models.generate_content.call_args
    assert call_kwargs[1]["model"] == "gemini-2.5-flash"


def test_suggest_endpoint_falls_back_to_mock_on_llm_failure(client, auth_headers):
    """
    LLM呼び出しが失敗した場合にモックデータがフォールバックとして返ること。
    エラーが出ても /api/suggest は 200 を返す必要がある。
    """
    with patch("app.agents.recipe_generator.generate_meal_plan",
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
