"""各ニュース記事の代表サムネ画像(og:image 等)を取得する。

記事ページの HTML から OGP/Twitter カードの画像メタタグを抽出する。
依存を増やさないため正規表現で軽量にパースし、取得は並列で行う。
画像が見つからない場合は空文字を返し、HTML 側で favicon にフォールバックする。
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests

from .models import Source

logger = logging.getLogger(__name__)

# <meta property="og:image" content="..."> 等を順に探す
_META_PATTERNS = [
    re.compile(r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']', re.I),
]

# 記事の公開日時メタ(優先順)。published > JSON-LD > 更新時刻 > time タグ。
_DATE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']', re.I),
    re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I),
    re.compile(r'<meta[^>]+name=["\'](?:pubdate|publishdate|publish-date|date)["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+property=["\']og:updated_time["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'"dateModified"\s*:\s*"([^"]+)"', re.I),
    re.compile(r'<time[^>]+datetime=["\']([^"\']+)["\']', re.I),
]

# ヒーロー表示(幅いっぱい)に耐える最小サイズ。これ未満は拡大ボケするので不採用。
_MIN_THUMB_W = 360
_MIN_THUMB_H = 180

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; report-agent/0.1; +local)",
    "Accept": "text/html,application/xhtml+xml",
}


def favicon_url(page_url: str, size: int = 128) -> str:
    """記事URLのドメインから favicon URL を組み立てる(フォールバック用)。"""
    host = urlparse(page_url).hostname or ""
    if not host:
        return ""
    return f"https://www.google.com/s2/favicons?domain={host}&sz={size}"


def fetch_page_meta(url: str, timeout: int = 6) -> tuple[str, str]:
    """記事ページを1回取得し、(og:image, 公開日時文字列) を返す。失敗時は空。"""
    try:
        resp = requests.get(
            url, headers=_HEADERS, timeout=timeout, stream=True, allow_redirects=True
        )
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype:
            return "", ""
        # 先頭の一部だけ読めば <head> のメタタグは取れる
        chunk = resp.raw.read(120_000, decode_content=True)
        resp.close()
        html = chunk.decode(resp.encoding or "utf-8", errors="ignore")
        head = html.split("</head>")[0] if "</head>" in html else html

        img = ""
        for pat in _META_PATTERNS:
            m = pat.search(head)
            if m:
                cand = m.group(1).strip()
                if cand.startswith("//"):
                    cand = "https:" + cand
                elif cand.startswith("/"):
                    cand = urljoin(url, cand)
                if cand.startswith("http"):
                    img = cand
                    break

        published = ""
        for pat in _DATE_PATTERNS:
            m = pat.search(html)
            if m and m.group(1).strip():
                published = m.group(1).strip()
                break
        return img, published
    except Exception as e:  # noqa: BLE001 - 取得失敗は致命的でない
        logger.debug("ページメタ取得失敗 %s: %s", url, e)
        return "", ""


def fetch_og_image(url: str, timeout: int = 6) -> str:
    """記事ページから og:image を取得(後方互換)。"""
    return fetch_page_meta(url, timeout=timeout)[0]


def _is_good_thumbnail(img_url: str, timeout: int = 6) -> bool:
    """画像が幅いっぱい表示に耐えるサイズか判定。

    Pillow が無い／サイズを判定できない場合は True(採用)を返す。
    明確に小さい場合だけ False(不採用→favicon フォールバック)。
    """
    try:
        from PIL import Image
    except ImportError:
        return True
    try:
        resp = requests.get(img_url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()
        import io
        w, h = Image.open(io.BytesIO(resp.content)).size
        return w >= _MIN_THUMB_W and h >= _MIN_THUMB_H
    except Exception:  # noqa: BLE001 - 判定不能なら採用(従来通り)
        return True


def enrich_sources(
    sources: list[Source], max_workers: int = 8, timeout: int = 6,
    want_date: bool = True,
) -> int:
    """各記事ページを1回取得し、image_url(og:image) と published_at(公開日時) を埋める。

    画像が小さすぎる(ロゴ等)場合は不採用にして favicon に任せる。
    published_at は SearXNG が返さないため、ここで meta から補う(鮮度判定に使用)。
    画像取得数を返す。
    """
    targets = [s for s in sources if s.url and (not s.image_url or want_date)]

    def work(s: Source):
        img, published = fetch_page_meta(s.url, timeout=timeout)
        if not s.image_url:
            s.image_url = img if (img and _is_good_thumbnail(img, timeout=timeout)) else ""
        if want_date and published and not s.published_at:
            s.published_at = published

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(work, targets))

    got = sum(1 for s in targets if s.image_url)
    dated = sum(1 for s in targets if s.published_at)
    logger.info("[thumbs] %d/%d 件のサムネ取得 / %d 件に公開日時", got, len(targets), dated)
    return got


def enrich_thumbnails(sources: list[Source], max_workers: int = 8, timeout: int = 6) -> int:
    """後方互換: 画像のみ補完(日付は埋めない)。"""
    return enrich_sources(sources, max_workers, timeout, want_date=False)
