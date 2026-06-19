"""HTML を PDF に変換する。

スマホでローカル HTML を開くと外部画像を取得できないことがあるため、
PDF では画像を base64 で焼き込み、スマホで読みやすいページ寸法・フォントにする。
"""

from __future__ import annotations

import base64
import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

# PDF 埋め込み画像の最大長辺(px)。狭いページ表示には十分で、ファイルサイズを抑える。
_MAX_IMG_DIM = 560
_JPEG_QUALITY = 78

# スマホ閲覧向けレイアウト(@page は単一ページ化のため別途指定する)。
_PAGE_WIDTH_MM = 105
_PAGE_MARGIN_MM = 6
# PDF の1ページ最大寸法は約 5080mm。これを超える場合は通常ページ分割にフォールバック。
_PDF_MAX_PAGE_MM = 5000

_PDF_CSS = """
html { font-size: 12.5px; }
body { max-width: none !important; margin: 0 !important; padding: 0 !important; }
h1 { font-size: 1.45rem !important; }
h2 { font-size: 1.18rem !important; }
article.news h3 { font-size: 1rem !important; }
/* すべての画像をページ幅に収める */
img { max-width: 100% !important; }
/* 狭いページでは画像を上、本文を下に積む。サムネは横幅いっぱい */
article.news { flex-direction: column !important; gap: .4rem !important; page-break-inside: avoid; }
article.news .thumb { flex: none !important; width: 100% !important; }
/* サムネは高さ固定でページ幅いっぱい(レイアウトを一定化し総高さを抑える) */
article.news .thumb img { width: 100% !important; height: 44mm !important;
  object-fit: cover; }
/* 良いサムネが無いカードは、faviconを引き伸ばさず小さく中央に(細い帯) */
article.news .thumb-fav { width: 100% !important; height: 16mm !important;
  display: flex !important; align-items: center !important; justify-content: center !important;
  background: #f1f5f9 !important; }
article.news .thumb-fav img { width: auto !important; height: 32px !important;
  object-fit: contain !important; }
/* 冒頭の関連画像: weasyprint の grid は幅崩れするため inline-block 2列に固定 */
.gallery { display: block !important; font-size: 0 !important; }
.gallery figure { display: inline-block !important; width: 48% !important;
  margin: 0 1% 2% 1% !important; vertical-align: top !important; overflow: hidden !important; }
.gallery img { width: 100% !important; height: 34mm !important; object-fit: cover !important; }
.gallery figcaption { font-size: 9px !important; white-space: normal !important; }
.toc ul { columns: 1 !important; }
header.brand .tagline { display: none; }
.ttk-spin { animation: none !important; }
"""


def _page_css(width_mm: float, height_mm: float, margin_mm: float) -> str:
    return f"@page {{ size: {width_mm}mm {height_mm}mm; margin: {margin_mm}mm; }}"


def _content_height_mm(page) -> float:
    """レンダリング済みページから、内側コンテンツの高さ(mm)を求める。"""
    def bottom(box) -> float:
        b = (getattr(box, "position_y", 0) or 0) + (getattr(box, "height", 0) or 0)
        for c in getattr(box, "children", None) or []:
            b = max(b, bottom(c))
        return b
    page_box = page._page_box  # @page ボックス。children が実コンテンツ
    inner_px = max((bottom(c) for c in page_box.children), default=0)
    margin_px = _PAGE_MARGIN_MM * 96 / 25.4
    total_px = inner_px + margin_px + 6  # 下マージン＋わずかな余裕
    return total_px * 25.4 / 96

_IMG_SRC = re.compile(r'src=(["\'])(https?://[^"\']+)\1')


def _downscale(content: bytes) -> tuple[bytes, str]:
    """画像を最大長辺 _MAX_IMG_DIM に縮小し JPEG/PNG へ再エンコード。

    縮小できない場合(SVG 等)は元データをそのまま返す。
    """
    try:
        from PIL import Image
    except ImportError:
        return content, "image/jpeg"
    try:
        im = Image.open(io.BytesIO(content))
        has_alpha = im.mode in ("RGBA", "LA", "P")
        im.thumbnail((_MAX_IMG_DIM, _MAX_IMG_DIM))
        out = io.BytesIO()
        if has_alpha:
            im.convert("RGBA").save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png"
        im.convert("RGB").save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return out.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - 縮小失敗時は元データ
        return content, "image/jpeg"


def _to_data_uri(url: str, timeout: int = 8) -> str | None:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "report-agent/0.1"})
        resp.raise_for_status()
        content, ctype = _downscale(resp.content)
        b64 = base64.b64encode(content).decode("ascii")
        return f"data:{ctype};base64,{b64}"
    except Exception as e:  # noqa: BLE001 - 画像取得失敗は致命的でない
        logger.debug("画像の取り込み失敗 %s: %s", url, e)
        return None


def _inline_images(html: str, max_workers: int = 10) -> str:
    """HTML 内の外部画像 URL を base64 data URI に置換(PDF へ焼き込み)。"""
    urls = list({m.group(2) for m in _IMG_SRC.finditer(html)})
    if not urls:
        return html
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        data = dict(zip(urls, ex.map(_to_data_uri, urls)))
    ok = sum(1 for v in data.values() if v)
    logger.info("[pdf] 画像 %d/%d 件を埋め込み", ok, len(urls))

    def repl(m: re.Match) -> str:
        q, url = m.group(1), m.group(2)
        uri = data.get(url)
        return f"src={q}{uri}{q}" if uri else m.group(0)

    return _IMG_SRC.sub(repl, html)


def _inject_pdf_css(html: str) -> str:
    style = f"<style>{_PDF_CSS}</style>"
    if "</head>" in html:
        return html.replace("</head>", style + "</head>", 1)
    return style + html


def html_to_pdf(html: str, out_path: str, single_page: bool = True) -> str:
    """HTML 文字列を PDF 化して out_path に書き出す。

    single_page=True のときは、コンテンツ全体を1枚の縦長ページに収める
    (スマホでスクロール閲覧でき、ページ分割による余白が出ない)。
    高さが PDF の上限を超える場合は通常のページ分割にフォールバックする。
    """
    import weasyprint  # 遅延 import(重い依存のため)

    html = _inline_images(html)
    html = _inject_pdf_css(html)
    base = weasyprint.HTML(string=html)
    w, m = _PAGE_WIDTH_MM, _PAGE_MARGIN_MM

    if single_page:
        # 1パス目: 非常に高いページで全体を1ページに流し込み、実コンテンツ高さを測る
        probe = base.render(stylesheets=[weasyprint.CSS(string=_page_css(w, 100000, m))])
        height_mm = _content_height_mm(probe.pages[0])
        if height_mm <= _PDF_MAX_PAGE_MM:
            # 2パス目: 実寸の高さで1ページとして出力
            doc = base.render(stylesheets=[weasyprint.CSS(string=_page_css(w, round(height_mm, 1), m))])
            doc.write_pdf(out_path)
            logger.info("[pdf] 単一ページ出力 (高さ %.0fmm)", height_mm)
            return out_path
        logger.info(
            "[pdf] 高さ %.0fmm が上限超過のため通常ページ分割で出力", height_mm
        )

    # フォールバック: 通常の縦長ページで分割
    doc = base.render(stylesheets=[weasyprint.CSS(string=_page_css(w, 187, m))])
    doc.write_pdf(out_path)
    return out_path
