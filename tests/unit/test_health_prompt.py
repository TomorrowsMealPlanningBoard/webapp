"""
Issue #25: 健康データを考慮したプロンプトチューニング ユニットテスト

- health_data=None のとき、プロンプトに健康関連の注入がないこと
- protein_g=30 のとき、高タンパク指示が注入されること
- fat_g=90 のとき、低脂質指示が注入されること
- carbs_g=350 のとき、炭水化物控えめ指示が注入されること
- calories=3000 のとき、軽めメニュー指示が注入されること
- 全てのデータが正常値のとき、余分な注入がないこと
"""
import pytest

from app.agents.health_api import HealthData
from app.agents.recipe_generator import _build_prompt
from app.agents.context_retriever import (
    RetrievedContext,
    HardConstraints,
    StructuredFeedbackContext,
)
from app.schemas import SuggestRequest


@pytest.fixture
def base_request():
    return SuggestRequest(
        cooking_time=30,
        effort_level="normal",
        mood_tags=[],
        mood_freetext="",
        ingredients=[],
    )


@pytest.fixture
def base_context():
    return RetrievedContext(
        user_id="test-user-health",
        hard_constraints=HardConstraints(
            allergies=[],
            forbidden_ingredients=[],
            available_kitchen_tools=[],
        ),
        structured_feedback=StructuredFeedbackContext(
            negative_tags=[],
            positive_tags=[],
        ),
        similar_snippets=[],
        health_data=None,
    )


def test_no_health_injection_when_health_data_is_none(base_request, base_context):
    prompt = _build_prompt(base_request, base_context)
    assert "タンパク質が不足" not in prompt
    assert "脂質が多かった" not in prompt
    assert "炭水化物が多かった" not in prompt
    assert "カロリー摂取量が多かった" not in prompt
    assert "特になし" in prompt


def test_high_protein_injection_when_protein_low(base_request, base_context):
    base_context.health_data = HealthData(protein_g=30.0)
    prompt = _build_prompt(base_request, base_context)
    assert "タンパク質が不足しています" in prompt
    assert "30.0g" in prompt
    assert "高タンパクなメニューを優先してください" in prompt


def test_low_fat_injection_when_fat_high(base_request, base_context):
    base_context.health_data = HealthData(fat_g=90.0)
    prompt = _build_prompt(base_request, base_context)
    assert "脂質が多かったです" in prompt
    assert "90.0g" in prompt
    assert "低脂質なメニューを意識してください" in prompt


def test_low_carb_injection_when_carbs_high(base_request, base_context):
    base_context.health_data = HealthData(carbs_g=350.0)
    prompt = _build_prompt(base_request, base_context)
    assert "炭水化物が多かったです" in prompt
    assert "350.0g" in prompt
    assert "炭水化物控えめのメニューを優先してください" in prompt


def test_light_menu_injection_when_calories_high(base_request, base_context):
    base_context.health_data = HealthData(calories=3000.0)
    prompt = _build_prompt(base_request, base_context)
    assert "カロリー摂取量が多かったです" in prompt
    assert "3000kcal" in prompt
    assert "軽めのメニューを優先してください" in prompt


def test_no_extra_injection_when_all_values_normal(base_request, base_context):
    base_context.health_data = HealthData(
        calories=2000.0,
        protein_g=60.0,
        fat_g=70.0,
        carbs_g=250.0,
    )
    prompt = _build_prompt(base_request, base_context)
    assert "タンパク質が不足" not in prompt
    assert "脂質が多かった" not in prompt
    assert "炭水化物が多かった" not in prompt
    assert "カロリー摂取量が多かった" not in prompt
    assert "特になし" in prompt


def test_multiple_notes_injected_when_multiple_thresholds_exceeded(base_request, base_context):
    base_context.health_data = HealthData(
        calories=3000.0,
        protein_g=30.0,
        fat_g=90.0,
        carbs_g=350.0,
    )
    prompt = _build_prompt(base_request, base_context)
    assert "タンパク質が不足しています" in prompt
    assert "脂質が多かったです" in prompt
    assert "炭水化物が多かったです" in prompt
    assert "カロリー摂取量が多かったです" in prompt


def test_no_injection_when_health_data_fields_are_none(base_request, base_context):
    base_context.health_data = HealthData()
    prompt = _build_prompt(base_request, base_context)
    assert "タンパク質が不足" not in prompt
    assert "脂質が多かった" not in prompt
    assert "炭水化物が多かった" not in prompt
    assert "カロリー摂取量が多かった" not in prompt
    assert "特になし" in prompt
