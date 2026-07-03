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
