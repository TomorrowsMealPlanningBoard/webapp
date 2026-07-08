"""
Issue #32/#78: お気に入りレシピソース（外部URL）の取り込みユニットテスト。
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agents.context_retriever import ContextRetrieverAgent
from app.agents.source_extractor import ExtractedSourceProfile, extract_profile
from app.agents.source_scraper import (
    ScrapedSource,
    SourceScrapeError,
    _detect_source_type,
    _extract_youtube_video_id,
    scrape_source,
)


# ============================================================
# 1. URLスクレイピング
# ============================================================

def test_detect_source_type_youtube():
    assert _detect_source_type("https://www.youtube.com/watch?v=abc123") == "youtube"
    assert _detect_source_type("https://youtu.be/abc123") == "youtube"


def test_detect_source_type_blog():
    assert _detect_source_type("https://example.com/recipe/123") == "blog"


def test_detect_source_type_rejects_non_http_scheme():
    with pytest.raises(SourceScrapeError):
        _detect_source_type("ftp://example.com/file")


def test_extract_youtube_video_id_from_watch_url():
    assert _extract_youtube_video_id("https://www.youtube.com/watch?v=abc123") == "abc123"


def test_extract_youtube_video_id_from_short_url():
    assert _extract_youtube_video_id("https://youtu.be/abc123") == "abc123"


def test_scrape_blog_extracts_title_and_text():
    html = """
    <html><head><title>簡単！豚肉と玉ねぎの甘辛炒め</title></head>
    <body><nav>ナビ</nav><article>醤油とみりんで甘辛く仕上げます。</article></body></html>
    """
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.headers = {"content-type": "text/html; charset=utf-8"}
    mock_response.raise_for_status.return_value = None
    mock_client.get.return_value = mock_response

    result = scrape_source("https://example.com/recipe/123", client=mock_client)

    assert result.source_type == "blog"
    assert "豚肉と玉ねぎ" in result.title
    assert "醤油とみりん" in result.text_content
    assert "ナビ" not in result.text_content


def test_scrape_blog_raises_on_http_error():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = httpx.ConnectError("connection failed")

    with pytest.raises(SourceScrapeError):
        scrape_source("https://example.com/unreachable", client=mock_client)


def test_scrape_blog_raises_on_non_html_content_type():
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock()
    mock_response.headers = {"content-type": "application/pdf"}
    mock_response.raise_for_status.return_value = None
    mock_client.get.return_value = mock_response

    with pytest.raises(SourceScrapeError):
        scrape_source("https://example.com/file.pdf", client=mock_client)


def test_scrape_youtube_uses_oembed_for_title():
    mock_client = MagicMock(spec=httpx.Client)

    oembed_response = MagicMock()
    oembed_response.raise_for_status.return_value = None
    oembed_response.json.return_value = {
        "title": "鶏肉の甘辛照り焼き作ってみた",
        "author_name": "料理チャンネル",
    }

    captions_response = MagicMock()
    captions_response.status_code = 404
    captions_response.text = ""

    mock_client.get.side_effect = [oembed_response, captions_response]

    result = scrape_source("https://www.youtube.com/watch?v=abc123", client=mock_client)

    assert result.source_type == "youtube"
    assert result.title == "鶏肉の甘辛照り焼き作ってみた"
    assert "料理チャンネル" in result.text_content


def test_scrape_youtube_raises_when_no_video_id():
    mock_client = MagicMock(spec=httpx.Client)
    with pytest.raises(SourceScrapeError):
        scrape_source("https://www.youtube.com/channel/xyz", client=mock_client)


def test_scrape_youtube_raises_on_oembed_failure():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.side_effect = httpx.HTTPStatusError(
        "not found", request=MagicMock(), response=MagicMock(status_code=404)
    )

    with pytest.raises(SourceScrapeError):
        scrape_source("https://www.youtube.com/watch?v=deadvideo", client=mock_client)


def test_scrape_source_rejects_unsupported_url():
    with pytest.raises(SourceScrapeError):
        scrape_source("not-a-valid-url")


# ============================================================
# 2. LLMによる傾向抽出
# ============================================================

_VALID_EXTRACTION_RESPONSE = """
{
  "seasoning_tendency": "醤油とみりんベースの甘辛い味付けを好む傾向",
  "favorite_ingredient_combos": ["豚肉と玉ねぎ", "鶏肉とねぎ"],
  "cooking_style": "短時間で作れる炒め物中心",
  "tags": ["和食", "時短"]
}
"""


def test_extract_profile_parses_valid_llm_response():
    scraped = ScrapedSource(
        url="https://example.com/recipe",
        source_type="blog",
        title="豚肉と玉ねぎの甘辛炒め",
        text_content="醤油とみりんで甘辛く仕上げる炒め物レシピです。",
    )

    mock_response = MagicMock()
    mock_response.text = _VALID_EXTRACTION_RESPONSE

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.source_extractor._get_client", return_value=mock_client):
        profile = extract_profile(scraped)

    assert isinstance(profile, ExtractedSourceProfile)
    assert "甘辛い" in profile.seasoning_tendency
    assert "豚肉と玉ねぎ" in profile.favorite_ingredient_combos
    assert profile.cooking_style == "短時間で作れる炒め物中心"
    assert "和食" in profile.tags


def test_extract_profile_raises_on_api_error():
    from google.genai import errors as genai_errors

    scraped = ScrapedSource(
        url="https://example.com", source_type="blog", title="t", text_content="c"
    )
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = genai_errors.APIError(
        code=500, response_json={"error": {"message": "Internal error"}}
    )

    with patch("app.agents.source_extractor._get_client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Gemini API"):
            extract_profile(scraped)


def test_extract_profile_raises_on_empty_response():
    scraped = ScrapedSource(
        url="https://example.com", source_type="blog", title="t", text_content="c"
    )
    mock_response = MagicMock()
    mock_response.text = ""

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.source_extractor._get_client", return_value=mock_client):
        with pytest.raises(ValueError, match="空のレスポンス"):
            extract_profile(scraped)


def test_extract_profile_raises_on_invalid_json():
    scraped = ScrapedSource(
        url="https://example.com", source_type="blog", title="t", text_content="c"
    )
    mock_response = MagicMock()
    mock_response.text = "これはJSONではありません"

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response

    with patch("app.agents.source_extractor._get_client", return_value=mock_client):
        with pytest.raises(ValueError):
            extract_profile(scraped)


def test_extracted_profile_to_snippet_text_includes_all_fields():
    profile = ExtractedSourceProfile(
        seasoning_tendency="甘辛い味付けを好む",
        favorite_ingredient_combos=["豚肉と玉ねぎ"],
        cooking_style="短時間の炒め物",
        tags=["和食"],
    )
    text = profile.to_snippet_text("テスト動画")
    assert "甘辛い味付けを好む" in text
    assert "豚肉と玉ねぎ" in text
    assert "短時間の炒め物" in text
    assert "テスト動画" in text


# ============================================================
# 3. POST /api/sources エンドポイント
# ============================================================

def test_post_sources_success_saves_to_db(client, auth_headers, test_user, mock_firestore):
    scraped = ScrapedSource(
        url="https://example.com/recipe",
        source_type="blog",
        title="豚肉と玉ねぎの甘辛炒め",
        text_content="醤油とみりんで甘辛く仕上げる炒め物レシピです。",
    )
    profile = ExtractedSourceProfile(
        seasoning_tendency="醤油とみりんベースの甘辛い味付け",
        favorite_ingredient_combos=["豚肉と玉ねぎ"],
        cooking_style="短時間の炒め物中心",
        tags=["和食", "時短"],
    )

    with patch("app.main.scrape_source", return_value=scraped), \
         patch("app.main.source_extractor_module.extract_profile", return_value=profile):
        res = client.post(
            "/api/sources",
            headers=auth_headers,
            json={"url": "https://example.com/recipe"},
        )

    assert res.status_code == 200
    body = res.json()
    assert body["source_type"] == "blog"
    assert body["title"] == "豚肉と玉ねぎの甘辛炒め"
    assert "甘辛い" in body["seasoning_tendency"]
    assert "豚肉と玉ねぎ" in body["favorite_ingredient_combos"]
    assert "和食" in body["tags"]

    saved = mock_firestore.recipe_sources.get(test_user.uid, [])
    assert len(saved) == 1
    assert saved[0]["status"] == "completed"
    assert saved[0]["summary_text"]


def test_post_sources_scrape_failure_returns_422_and_does_not_save(
    client, auth_headers, test_user, mock_firestore
):
    with patch("app.main.scrape_source", side_effect=SourceScrapeError("非対応のURLです")):
        res = client.post(
            "/api/sources",
            headers=auth_headers,
            json={"url": "https://unsupported.example.com"},
        )

    assert res.status_code == 422
    assert len(mock_firestore.recipe_sources.get(test_user.uid, [])) == 0


def test_post_sources_llm_extraction_failure_returns_422_and_does_not_save(
    client, auth_headers, test_user, mock_firestore
):
    scraped = ScrapedSource(
        url="https://example.com", source_type="blog", title="t", text_content="c"
    )

    with patch("app.main.scrape_source", return_value=scraped), \
         patch(
             "app.main.source_extractor_module.extract_profile",
             side_effect=RuntimeError("LLM失敗"),
         ):
        res = client.post(
            "/api/sources",
            headers=auth_headers,
            json={"url": "https://example.com"},
        )

    assert res.status_code == 422
    assert len(mock_firestore.recipe_sources.get(test_user.uid, [])) == 0


def test_post_sources_requires_auth(client):
    res = client.post("/api/sources", json={"url": "https://example.com"})
    assert res.status_code in (401, 403)


def test_post_sources_does_not_affect_existing_suggest_flow(client, auth_headers):
    with patch("app.main.scrape_source", side_effect=SourceScrapeError("失敗")):
        res = client.post("/api/sources", headers=auth_headers, json={"url": "https://bad.example.com"})
    assert res.status_code == 422

    suggest_res = client.post(
        "/api/suggest",
        headers=auth_headers,
        json={
            "cooking_time": 30,
            "effort_level": "normal",
            "mood_tags": [],
            "mood_freetext": "",
            "ingredients": [],
        },
    )
    assert suggest_res.status_code == 200


# ============================================================
# 4. Context Retriever Agent との統合
# ============================================================

def test_context_retriever_returns_all_favorite_recipe_sources(mock_firestore):
    mock_firestore.add_user(uid="source-user-001", email="source-user-001@example.com")
    mock_firestore.add_recipe_source(
        user_id="source-user-001",
        id="src-1",
        summary_text="醤油とみりんベースの甘辛い炒め物を好む。豚肉と玉ねぎの組み合わせが多い。",
        tags=["和食", "時短"],
    )

    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="source-user-001", query_text="豚肉を使った炒め物レシピ"))

    assert len(result.favorite_recipe_sources) == 1
    assert result.favorite_recipe_sources[0].seasoning_tendency == (
        "醤油とみりんベースの甘辛い炒め物を好む。豚肉と玉ねぎの組み合わせが多い。"
    )
    assert result.favorite_recipe_sources[0].tags == ["和食", "時短"]


def test_context_retriever_excludes_failed_sources_from_favorites(mock_firestore):
    mock_firestore.add_user(uid="source-user-002", email="source-user-002@example.com")
    mock_firestore.add_recipe_source(
        user_id="source-user-002", id="src-failed",
        summary_text="失敗したはずのソース", status="failed"
    )

    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="source-user-002", query_text="失敗したはずのソース"))

    assert result.favorite_recipe_sources == []


def test_context_retriever_excludes_other_users_favorite_sources(mock_firestore):
    mock_firestore.add_user(uid="src-user-a", email="src-user-a@example.com")
    mock_firestore.add_user(uid="src-user-b", email="src-user-b@example.com")
    mock_firestore.add_recipe_source(
        user_id="src-user-a", id="src-a",
        summary_text="ユーザーAの好み: 甘辛い味付け"
    )
    mock_firestore.add_recipe_source(
        user_id="src-user-b", id="src-b",
        summary_text="ユーザーBの好み: 塩味の効いた料理"
    )

    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="src-user-a", query_text="味付けの好み"))

    tendencies = [s.seasoning_tendency for s in result.favorite_recipe_sources]
    assert not any("ユーザーBの好み" in t for t in tendencies)


def test_context_retriever_favorite_sources_include_source_title_and_url(mock_firestore):
    mock_firestore.add_user(uid="source-user-003", email="source-user-003@example.com")
    mock_firestore.add_recipe_source(
        user_id="source-user-003", id="src-3",
        url="https://example.com/recipe",
        title="テストレシピ記事",
        summary_text="甘辛い味付けが好み",
    )

    agent = ContextRetrieverAgent()
    result = asyncio.run(agent.retrieve(user_id="source-user-003", query_text="味付けの好み"))

    assert result.favorite_recipe_sources[0].source_title == "テストレシピ記事"
    assert result.favorite_recipe_sources[0].source_url == "https://example.com/recipe"
