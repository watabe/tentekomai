"""SearXNG を使った情報収集。

SearXNG の JSON API (`/search?format=json`) を利用する。
テキスト結果(general)と画像結果(images)を取得する。
画像は HTML レポートに挿絵として埋め込むために収集する。
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import requests

from .models import ImageRef, Source

logger = logging.getLogger(__name__)


class SearchError(RuntimeError):
    pass


# 簡易な信頼度ヒューリスティクス(ドメインベース)。
_HIGH_TRUST = (
    ".go.jp", ".gov", ".edu", ".ac.jp", "wikipedia.org",
    "github.com", "arxiv.org",
)
_MID_TRUST = (
    "nikkei.com", "itmedia.co.jp", "publickey1.jp", "techcrunch.com",
    "theverge.com", "reuters.com", "bloomberg.com",
    # ニュース/テック/ゲーム系メディア(news モードで優先)
    "impress.co.jp", "ascii.jp", "famitsu.com", "dengeki.com",
    "4gamer.net", "automaton-media.com", "gizmodo.jp", "engadget.com",
    "gamer.ne.jp", "cnet.com",
)


def _reliability(url: str, news: bool = False) -> float:
    host = (urlparse(url).hostname or "").lower()
    if news:
        # news では「鮮度・報道性」を重視。権威系(政府/百科/論文)や PDF は優先しない。
        is_pdf = url.lower().split("?")[0].endswith(".pdf")
        if any(h in host for h in _MID_TRUST):
            score = 0.75
        elif any(h in host for h in _HIGH_TRUST):
            score = 0.4   # 権威はあるが古い資料が多く news 向きでない
        else:
            score = 0.55
        if is_pdf:
            score -= 0.2
        return round(max(0.1, score), 2)
    # 非 news(research 等)は従来通り
    if any(h in host for h in _HIGH_TRUST):
        return 0.9
    if any(h in host for h in _MID_TRUST):
        return 0.7
    return 0.5


class SearXNGClient:
    def __init__(
        self,
        base_url: str,
        max_results: int = 8,
        max_images: int = 6,
        language: str = "ja",
        timeout_sec: int = 30,
        time_range: str = "",
        news_mode: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_results = max_results
        self.max_images = max_images
        self.language = language
        self.timeout_sec = timeout_sec
        # 鮮度フィルタ(SearXNG): "" / day / week / month / year
        self.time_range = time_range or ""
        # news モード: 信頼度を報道性重視で計算(権威/古いPDFを優先しない)
        self.news_mode = news_mode

    def _get(self, params: dict) -> dict:
        url = f"{self.base_url}/search"
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=self.timeout_sec,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise SearchError(
                f"SearXNG への接続に失敗しました ({url}): {e}\n"
                f"SearXNG が起動しているか、--searxng-url を確認してください。"
            ) from e
        except ValueError as e:
            raise SearchError(
                f"SearXNG が JSON を返しませんでした。インスタンスの設定で "
                f"'json' フォーマットを有効化してください: {e}"
            ) from e

    def search_text(self, query: str) -> list[Source]:
        params = {
            "q": query,
            "format": "json",
            "language": self.language,
            "categories": "general",
        }
        if self.time_range:
            params["time_range"] = self.time_range
        data = self._get(params)
        sources: list[Source] = []
        for r in data.get("results", [])[: self.max_results]:
            url = r.get("url", "")
            if not url:
                continue
            sources.append(
                Source(
                    title=r.get("title", "") or url,
                    url=url,
                    summary=(r.get("content", "") or "").strip(),
                    published_at=r.get("publishedDate", "") or "",
                    reliability_score=_reliability(url, self.news_mode),
                    engine=r.get("engine", "") or "",
                )
            )
        return sources

    def search_images(self, query: str) -> list[ImageRef]:
        try:
            data = self._get(
                {
                    "q": query,
                    "format": "json",
                    "language": self.language,
                    "categories": "images",
                }
            )
        except SearchError as e:
            logger.warning("画像検索に失敗しました(継続します): %s", e)
            return []
        images: list[ImageRef] = []
        for r in data.get("results", [])[: self.max_images]:
            src = r.get("img_src") or r.get("thumbnail_src") or ""
            if not src or not src.startswith("http"):
                continue
            images.append(
                ImageRef(
                    title=r.get("title", "") or query,
                    img_src=src,
                    source_url=r.get("url", "") or "",
                )
            )
        return images
