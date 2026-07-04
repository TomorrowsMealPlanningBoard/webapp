"""
E2Eテスト: 冷蔵庫写真アップロードフローをブラウザから操作して検証する。

前提:
- アプリが http://localhost:8000 で起動済みであること
- playwright が導入済みであること

実行方法（headedモード）:
  uv run pytest tests/e2e/test_fridge_flow.py -v --headed

実行方法（headlessモード）:
  uv run pytest tests/e2e/test_fridge_flow.py -v
"""
import pytest
from pathlib import Path

BASE_URL = "http://localhost:8000"
SCREENSHOT_DIR = Path(__file__).parent.parent.parent / "e2e-screenshots"


def _screenshot(page, name: str):
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    print(f"[screenshot] {path}")


@pytest.fixture(scope="module")
def browser_page(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 420, "height": 900})
    page = context.new_page()
    yield page
    browser.close()


def _login(page, email="guest@example.com", password="password"):
    page.goto(BASE_URL)
    page.wait_for_selector("#login-email", timeout=5000)
    _screenshot(page, "01_login_page")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit-btn")
    page.wait_for_selector("#app-view:not(.hidden)", timeout=5000)
    _screenshot(page, "02_after_login")


def test_fridge_page_accessible(browser_page):
    """冷蔵庫タブに遷移できること"""
    _login(browser_page)
    browser_page.click("#nav-fridge")
    browser_page.wait_for_selector("#page-fridge:not(.hidden)", timeout=3000)
    _screenshot(browser_page, "03_fridge_page")
    assert browser_page.is_visible("#fridge-upload-area")


def test_fridge_upload_shows_preview(browser_page, tmp_path):
    """画像を選択するとプレビューが表示されること"""
    browser_page.click("#nav-fridge")
    browser_page.wait_for_selector("#page-fridge:not(.hidden)", timeout=3000)

    fake_jpg = tmp_path / "test_fridge.jpg"
    fake_jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)

    browser_page.set_input_files("#fridge-file-input", str(fake_jpg))
    browser_page.wait_for_selector("#fridge-preview:not(.hidden)", timeout=3000)
    _screenshot(browser_page, "04_fridge_preview")
    assert browser_page.is_visible("#fridge-preview")
    assert not browser_page.is_disabled("#fridge-analyze-btn")
    _screenshot(browser_page, "05_analyze_btn_enabled")


def test_fridge_analyze_btn_disabled_initially(playwright):
    """初期状態では分析ボタンが無効であること"""
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 420, "height": 900})
    page = context.new_page()
    try:
        page.goto(BASE_URL)
        page.fill("#login-email", "guest@example.com")
        page.fill("#login-password", "password")
        page.click("#login-submit-btn")
        page.wait_for_selector("#app-view:not(.hidden)", timeout=5000)
        page.click("#nav-fridge")
        page.wait_for_selector("#page-fridge:not(.hidden)", timeout=3000)
        _screenshot(page, "06_fridge_initial_state")
        assert page.is_disabled("#fridge-analyze-btn")
    finally:
        browser.close()


def test_fridge_ingredients_flow_into_suggest_request(playwright, tmp_path):
    """
    Issue #42: 冷蔵庫で認識した食材リストが state に保持され、
    「献立提案」実行時に /api/suggest のリクエストボディに含まれること。

    Vision APIと献立提案APIはネットワークレベルでモックし、
    フロントの配線（state保持 → リクエスト送信）のみを検証する。
    """
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 420, "height": 900})
    page = context.new_page()
    try:
        mock_ingredients = [
            {"name": "キャベツ", "quantity": 1, "unit": "個", "freshness": "good"},
            {"name": "豚肉", "quantity": 200, "unit": "g", "freshness": "fair"},
        ]

        def handle_vision(route):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=__import__("json").dumps({"ingredients": mock_ingredients}),
            )

        captured_suggest_body = {}

        def handle_suggest(route):
            captured_suggest_body["body"] = route.request.post_data_json
            route.fulfill(
                status=200,
                content_type="application/json",
                body=__import__("json").dumps({"recipes": [], "message": "テスト提案"}),
            )

        page.route("**/api/vision", handle_vision)
        page.route("**/api/suggest", handle_suggest)

        page.goto(BASE_URL)
        page.fill("#login-email", "guest@example.com")
        page.fill("#login-password", "password")
        page.click("#login-submit-btn")
        page.wait_for_selector("#app-view:not(.hidden)", timeout=5000)

        # 冷蔵庫ページで画像をアップロードして「解析」（モック応答）
        page.click("#nav-fridge")
        page.wait_for_selector("#page-fridge:not(.hidden)", timeout=3000)
        fake_jpg = tmp_path / "test_fridge.jpg"
        fake_jpg.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
        page.set_input_files("#fridge-file-input", str(fake_jpg))
        page.wait_for_selector("#fridge-preview:not(.hidden)", timeout=3000)
        page.click("#fridge-analyze-btn")
        page.wait_for_selector("#fridge-result:not(.hidden)", timeout=5000)
        _screenshot(page, "07_fridge_analyzed")

        # ページ内遷移（冷蔵庫 → 献立）をしても state が保持されることを確認しつつ献立提案を実行
        page.click("#nav-meal")
        page.wait_for_selector("#page-meal:not(.hidden)", timeout=3000)
        page.click("#suggest-btn")
        page.wait_for_selector("#suggest-message:not(.hidden)", timeout=5000)
        _screenshot(page, "08_suggest_after_fridge")

        assert "body" in captured_suggest_body, "/api/suggest が呼ばれていません"
        sent_ingredients = captured_suggest_body["body"].get("ingredients")
        assert sent_ingredients == mock_ingredients, (
            f"献立提案リクエストに食材リストが含まれていません: {sent_ingredients}"
        )
    finally:
        browser.close()
