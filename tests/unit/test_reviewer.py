"""
Issue #30: Recipe Reviewer Agent（層1決定的ガードレールと差し戻しLoop）ユニットテスト

観点:
    - アレルギー物質混入 / 除外指定タグ含有 / 未所持調理器具使用を決定的に検出する
    - 違反時は理由付きで regenerate_fn（Generatorのモック）に差し戻し、再検査する
    - 最大リトライ回数上限でフォールバック（非承認）する
    - 全案が制約をクリアして初めてレスポンス可能と判定できる
    - 検査ロジックが確率的処理（乱数・埋め込み類似度等）に依存しないこと
"""
from __future__ import annotations

import random
from typing import List

import pytest

from app.agents.reviewer import (
    ReviewProfile,
    ViolationType,
    check_recipe,
    review_recipe_with_retries,
    review_recipes,
)
from app.schemas import Recipe, RecipeStep


def _make_recipe(
    id="r1",
    title="鶏むね肉のさっぱりレモン炒め",
    tags=None,
    ingredients=None,
    required_tools=None,
) -> Recipe:
    return Recipe(
        id=id,
        title=title,
        emoji="🍋",
        description="さっぱりとした炒め物",
        cooking_time=15,
        effort_level="normal",
        servings=2,
        tags=tags if tags is not None else ["さっぱり", "肉料理"],
        ingredients=ingredients if ingredients is not None else ["鶏むね肉 200g", "レモン汁 大さじ2"],
        steps=[RecipeStep(step=1, description="炒める")],
        nutrition_note=None,
        required_tools=required_tools if required_tools is not None else [],
    )


# ------------------------------------------------------------------ check_recipe --

def test_check_recipe_passes_when_no_violations():
    recipe = _make_recipe()
    profile = ReviewProfile(allergies=["卵"], negative_tags=["辛い"], kitchen_tools=["フライパン"])

    result = check_recipe(recipe, profile)

    assert result.is_valid
    assert result.violations == []


def test_check_recipe_detects_allergen_in_ingredients():
    """アレルギー物質（例: 卵）が材料に含まれる場合は違反として検出する"""
    recipe = _make_recipe(ingredients=["卵 2個", "牛乳 100ml"])
    profile = ReviewProfile(allergies=["卵"])

    result = check_recipe(recipe, profile)

    assert not result.is_valid
    assert any(v.type == ViolationType.ALLERGEN for v in result.violations)
    assert "卵" in result.violations[0].reason


def test_check_recipe_detects_allergen_in_title_or_description():
    """材料リストだけでなくタイトル・説明文中のアレルギー物質も検出する"""
    recipe = _make_recipe(title="えびとブロッコリーの炒め物", ingredients=["ブロッコリー 100g"])
    profile = ReviewProfile(allergies=["えび"])

    result = check_recipe(recipe, profile)

    assert not result.is_valid
    assert any(v.type == ViolationType.ALLERGEN for v in result.violations)


def test_check_recipe_detects_negative_tag():
    """除外指定タグ（negative_tags）を含む場合は違反として検出する"""
    recipe = _make_recipe(tags=["辛い", "肉料理"])
    profile = ReviewProfile(negative_tags=["辛い"])

    result = check_recipe(recipe, profile)

    assert not result.is_valid
    assert any(v.type == ViolationType.NEGATIVE_TAG for v in result.violations)


def test_check_recipe_detects_missing_kitchen_tool():
    """未所持の調理器具（例: オーブン）が必要な場合は違反として検出する"""
    recipe = _make_recipe(required_tools=["オーブン"])
    profile = ReviewProfile(kitchen_tools=["フライパン", "電子レンジ"])

    result = check_recipe(recipe, profile)

    assert not result.is_valid
    assert any(v.type == ViolationType.MISSING_TOOL for v in result.violations)
    assert "オーブン" in result.violations[0].reason


def test_check_recipe_passes_when_required_tool_is_owned():
    recipe = _make_recipe(required_tools=["オーブン"])
    profile = ReviewProfile(kitchen_tools=["オーブン"])

    result = check_recipe(recipe, profile)

    assert result.is_valid


def test_check_recipe_can_detect_multiple_violations_simultaneously():
    recipe = _make_recipe(
        title="えびのオーブン焼き",
        tags=["辛い"],
        ingredients=["えび 200g"],
        required_tools=["オーブン"],
    )
    profile = ReviewProfile(allergies=["えび"], negative_tags=["辛い"], kitchen_tools=[])

    result = check_recipe(recipe, profile)

    assert not result.is_valid
    types = {v.type for v in result.violations}
    assert types == {ViolationType.ALLERGEN, ViolationType.NEGATIVE_TAG, ViolationType.MISSING_TOOL}


def test_check_recipe_is_deterministic_not_probabilistic(monkeypatch):
    """層1検査が乱数などの確率的処理に依存しないことを保証する（設計原則）。
    random.random / random.uniform を例外を送出するようにパッチしても
    検査結果が変わらない（＝そもそも呼ばれない）ことを確認する。
    """
    def _boom(*args, **kwargs):
        raise AssertionError("check_recipe は乱数処理を呼び出してはならない")

    monkeypatch.setattr(random, "random", _boom)
    monkeypatch.setattr(random, "uniform", _boom)
    monkeypatch.setattr(random, "choice", _boom)

    recipe = _make_recipe(ingredients=["卵 2個"])
    profile = ReviewProfile(allergies=["卵"])

    result = check_recipe(recipe, profile)

    assert not result.is_valid


# ------------------------------------------------------------------ regenerate loop --

def test_review_recipe_with_retries_approves_immediately_when_valid():
    recipe = _make_recipe()
    profile = ReviewProfile(allergies=["卵"])
    calls = []

    def regenerate_fn(prev_recipe, reasons):
        calls.append((prev_recipe, reasons))
        return prev_recipe

    outcome = review_recipe_with_retries(recipe, profile, regenerate_fn, max_retries=2)

    assert outcome.approved
    assert outcome.recipe == recipe
    assert outcome.attempts == 1
    assert calls == []  # 差し戻しは発生しない


def test_review_recipe_with_retries_regenerates_and_approves_on_second_attempt():
    """1回目で違反 → 差し戻し → 2回目で違反解消レシピが返れば承認される"""
    bad_recipe = _make_recipe(ingredients=["卵 2個"])
    fixed_recipe = _make_recipe(id="r1-v2", ingredients=["豆腐 100g"])
    profile = ReviewProfile(allergies=["卵"])

    received_reasons: List[str] = []

    def regenerate_fn(prev_recipe, reasons):
        received_reasons.extend(reasons)
        return fixed_recipe

    outcome = review_recipe_with_retries(bad_recipe, profile, regenerate_fn, max_retries=2)

    assert outcome.approved
    assert outcome.recipe == fixed_recipe
    assert outcome.attempts == 2
    assert any("卵" in r for r in received_reasons)


def test_review_recipe_with_retries_passes_reason_to_generator():
    """差し戻し時、Generatorへ理由（violation reason）が渡されること"""
    bad_recipe = _make_recipe(tags=["辛い"])
    profile = ReviewProfile(negative_tags=["辛い"])

    captured = {}

    def regenerate_fn(prev_recipe, reasons):
        captured["reasons"] = reasons
        return _make_recipe(id="r1-v2", tags=["さっぱり"])

    review_recipe_with_retries(bad_recipe, profile, regenerate_fn, max_retries=1)

    assert captured["reasons"]
    assert "辛い" in captured["reasons"][0]


def test_review_recipe_with_retries_falls_back_after_max_retries():
    """再生成しても違反が解消しない場合、上限到達でフォールバック（非承認）する"""
    always_bad_recipe = _make_recipe(ingredients=["卵 2個"])
    profile = ReviewProfile(allergies=["卵"])
    call_count = 0

    def regenerate_fn(prev_recipe, reasons):
        nonlocal call_count
        call_count += 1
        # 常に違反を含んだレシピを返す（改善しないGeneratorを模擬）
        return _make_recipe(id=f"r1-v{call_count+1}", ingredients=["卵 3個"])

    outcome = review_recipe_with_retries(always_bad_recipe, profile, regenerate_fn, max_retries=2)

    assert not outcome.approved
    assert outcome.recipe is None
    assert outcome.fallback_used
    # 初回 + リトライ2回 = 3回検査
    assert outcome.attempts == 3
    # リトライ回数分だけ regenerate_fn が呼ばれる（3回目は差し戻さない）
    assert call_count == 2


def test_review_recipe_with_retries_respects_max_retries_configuration():
    """最大リトライ回数の設定値が尊重されること"""
    always_bad_recipe = _make_recipe(ingredients=["卵 2個"])
    profile = ReviewProfile(allergies=["卵"])
    call_count = 0

    def regenerate_fn(prev_recipe, reasons):
        nonlocal call_count
        call_count += 1
        return _make_recipe(id=f"r1-v{call_count+1}", ingredients=["卵 3個"])

    outcome = review_recipe_with_retries(always_bad_recipe, profile, regenerate_fn, max_retries=0)

    assert not outcome.approved
    assert outcome.attempts == 1
    assert call_count == 0  # リトライ0回なので再生成は一切呼ばれない


# ------------------------------------------------------------------ session (3案) --

def test_review_recipes_all_approved_when_all_valid():
    recipes = [_make_recipe(id=f"r{i}") for i in range(3)]
    profile = ReviewProfile(allergies=["卵"])

    def regenerate_fn(prev_recipe, reasons):
        return prev_recipe

    session = review_recipes(recipes, profile, regenerate_fn, max_retries=2)

    assert session.all_approved
    assert len(session.approved_recipes) == 3


def test_review_recipes_not_all_approved_if_one_fails_permanently():
    """1案でも承認できない場合、全案クリアと判定されない（フロントに一括レスポンスしない設計）"""
    good_recipe = _make_recipe(id="good")
    bad_recipe = _make_recipe(id="bad", ingredients=["卵 2個"])
    profile = ReviewProfile(allergies=["卵"])

    def regenerate_fn(prev_recipe, reasons):
        # 卵入りレシピはどう再生成しても直らない
        if prev_recipe.id.startswith("bad"):
            return _make_recipe(id=prev_recipe.id + "-v2", ingredients=["卵 1個"])
        return prev_recipe

    session = review_recipes([good_recipe, bad_recipe], profile, regenerate_fn, max_retries=1)

    assert not session.all_approved
    assert len(session.approved_recipes) == 1
    assert session.approved_recipes[0].id == "good"


def test_review_recipes_each_recipe_reviewed_independently():
    """3案それぞれが独立して検査されること（他の案の違反に影響されない）"""
    recipes = [
        _make_recipe(id="ok1"),
        _make_recipe(id="violating", ingredients=["卵 1個"]),
        _make_recipe(id="ok2"),
    ]
    profile = ReviewProfile(allergies=["卵"])

    def regenerate_fn(prev_recipe, reasons):
        return _make_recipe(id=prev_recipe.id + "-fixed", ingredients=["豆腐 100g"])

    session = review_recipes(recipes, profile, regenerate_fn, max_retries=2)

    assert session.all_approved
    ids = {r.id for r in session.approved_recipes}
    assert "ok1" in ids
    assert "ok2" in ids
    assert "violating-fixed" in ids
