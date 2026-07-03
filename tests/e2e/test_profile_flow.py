"""
E2Eテスト: プロファイル設定フローをブラウザから操作して検証する。

前提:
- アプリが http://localhost:8000 で起動済みであること
  （`docker compose up -d` または `uv run uvicorn app.main:app` を実行しておく）
- playwright が導入済みであること（`uv add pytest-playwright` + `playwright install chromium`）

実行方法:
  uv run pytest tests/e2e/ -v

Claude Code による自律検証時は、アプリ起動 → このテスト実行 → 結果確認 の順で行う。
"""
import pytest

BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="module")
def browser_page(playwright):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    yield page
    browser.close()


def test_top_page_loads(browser_page):
    """トップページが表示されること"""
    browser_page.goto(BASE_URL)
    assert browser_page.title() != ""


def test_login_flow(browser_page):
    """ログインしてプロファイルページに遷移できること"""
    browser_page.goto(BASE_URL)
    # ログインフォームが表示されていること
    browser_page.wait_for_selector("input[type='email'], input[name='username']", timeout=5000)
