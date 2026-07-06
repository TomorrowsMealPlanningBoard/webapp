"""
お気に入りレシピソース（外部URL）スクレイパー（Issue #32 / SPEC.md §5.4）。

設計方針:
- YouTube動画URLの場合は oEmbed API でタイトルを取得し、字幕（キャプション）取得を試みる。
  字幕が取得できない場合でもタイトル・概要のみでフォールバックする（完全失敗にしない）。
- ブログ等の一般URLの場合は HTML を取得し、<title> と本文らしきテキストを抽出する。
- 実際の外部ネットワークアクセスはテストでモック可能にするため、HTTPクライアントを
  `httpx.Client` 互換の薄いラッパー関数として注入可能にする
  （既存の `VectorSearchClient` Protocol による抽象化パターンに合わせる）。
- スクレイピング失敗・非対応URLの場合は `SourceScrapeError` を送出し、
  呼び出し側（/api/sources）でエラーレスポンスに変換する。既存の提案動作には影響しない。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

_YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}
_REQUEST_TIMEOUT = 10.0
_MAX_BODY_CHARS = 8000  # LLM抽出に渡すテキスト量の上限（トークン節約）


class SourceScrapeError(Exception):
    """スクレイピング失敗・非対応URLの場合に送出する。"""


@dataclass
class ScrapedSource:
    """スクレイピング結果。LLM抽出フェーズへの入力となる。"""

    url: str
    source_type: str  # "youtube" | "blog"
    title: str
    text_content: str  # 動画: 説明文+字幕 / ブログ: 本文テキスト


def _detect_source_type(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise SourceScrapeError(f"URLの解析に失敗しました: {e}") from e

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SourceScrapeError("http/https形式のURLを指定してください")

    if parsed.netloc in _YOUTUBE_HOSTS:
        return "youtube"
    return "blog"


def _extract_youtube_video_id(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.netloc == "youtu.be":
        video_id = parsed.path.lstrip("/")
        return video_id or None
    qs = parse_qs(parsed.query)
    if "v" in qs and qs["v"]:
        return qs["v"][0]
    return None


def _scrape_youtube(url: str, client: httpx.Client) -> ScrapedSource:
    video_id = _extract_youtube_video_id(url)
    if not video_id:
        raise SourceScrapeError("YouTubeの動画IDをURLから抽出できませんでした")

    # oEmbed APIでタイトルを取得（APIキー不要・公開エンドポイント）
    try:
        resp = client.get(
            "https://www.youtube.com/oembed",
            params={"url": url, "format": "json"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SourceScrapeError(f"YouTube動画情報の取得に失敗しました: {e}") from e

    title = data.get("title", "").strip()
    if not title:
        raise SourceScrapeError("YouTube動画のタイトルを取得できませんでした")

    author = data.get("author_name", "")
    description_parts = [title]
    if author:
        description_parts.append(f"投稿者: {author}")

    # 字幕取得はベストエフォート。取得できない場合もタイトル情報のみで処理を継続する
    # （非対応動画・字幕オフの動画でも全体を失敗させない）。
    captions = _try_fetch_youtube_captions(video_id, client)
    if captions:
        description_parts.append(captions)

    text_content = "\n".join(description_parts)[:_MAX_BODY_CHARS]
    return ScrapedSource(url=url, source_type="youtube", title=title, text_content=text_content)


def _try_fetch_youtube_captions(video_id: str, client: httpx.Client) -> str:
    """
    YouTube字幕（自動生成キャプション含む）の取得を試みる。
    取得できない場合は空文字列を返す（呼び出し側で失敗扱いにしない）。
    """
    try:
        resp = client.get(
            "https://video.google.com/timedtext",
            params={"lang": "ja", "v": video_id},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code != 200 or not resp.text.strip():
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        lines = [t.get_text() for t in soup.find_all("text")]
        return " ".join(lines).strip()
    except httpx.HTTPError:
        return ""


def _scrape_blog(url: str, client: httpx.Client) -> ScrapedSource:
    try:
        resp = client.get(url, timeout=_REQUEST_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SourceScrapeError(f"ページの取得に失敗しました: {e}") from e

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type and content_type:
        raise SourceScrapeError(f"非対応のコンテンツタイプです: {content_type}")

    soup = BeautifulSoup(resp.text, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.get_text().strip() if title_tag else ""

    # スクリプト・スタイルタグを除去してから本文らしきテキストを抽出する
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    body_text = soup.get_text(separator=" ", strip=True)
    body_text = re.sub(r"\s+", " ", body_text).strip()

    if not title and not body_text:
        raise SourceScrapeError("ページから本文を抽出できませんでした")

    if not title:
        title = url

    return ScrapedSource(
        url=url,
        source_type="blog",
        title=title,
        text_content=body_text[:_MAX_BODY_CHARS],
    )


def scrape_source(url: str, client: Optional[httpx.Client] = None) -> ScrapedSource:
    """
    URLをスクレイピングし、LLM抽出フェーズへの入力（タイトル・本文テキスト）を返す。

    Args:
        url: YouTube動画URL または ブログ記事等の一般URL
        client: 注入可能な httpx.Client（テストでモックする際に使用）。
                省略時は都度クライアントを生成する。

    Raises:
        SourceScrapeError: URLが非対応・取得失敗の場合
    """
    source_type = _detect_source_type(url)

    owns_client = client is None
    if owns_client:
        client = httpx.Client(
            headers={"User-Agent": "Mozilla/5.0 (compatible; TomorrowsMealBot/1.0)"}
        )

    try:
        if source_type == "youtube":
            return _scrape_youtube(url, client)
        return _scrape_blog(url, client)
    finally:
        if owns_client:
            client.close()
