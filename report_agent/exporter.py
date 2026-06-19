"""Exporter: 章を統合して Markdown と HTML を出力する。

HTML には収集した画像を挿絵として埋め込む(要件: 画像も表示したい)。
"""

from __future__ import annotations

import datetime as _dt
import html
import re

import markdown as md

from .models import ChapterResult, ImageRef, NewsCategory, Source

_CITATION = re.compile(r"\[S(\d+)\]")


def build_markdown(
    title: str,
    chapters: list[ChapterResult],
    sources: list[Source],
    keyword: str,
) -> str:
    today = _dt.date.today().isoformat()
    lines = [f"# {title}", ""]
    lines.append(f"*キーワード: {keyword} / 生成日: {today}*")
    lines.append("")

    # 目次
    lines.append("## 目次")
    for ch in chapters:
        lines.append(f"{ch.index + 1}. {ch.title}")
    lines.append("")

    # 本文
    for ch in chapters:
        lines.append(f"## {ch.index + 1}. {ch.title}")
        lines.append("")
        lines.append(ch.body)
        lines.append("")

    # 参考文献
    if sources:
        lines.append("## 参考文献")
        for i, s in enumerate(sources):
            date = f" ({s.published_at})" if s.published_at else ""
            lines.append(f"- [S{i}] [{s.title}]({s.url}){date}")
        lines.append("")

    return "\n".join(lines)


def _linkify_citations(text_html: str, sources: list[Source]) -> str:
    """本文中の [S番号] を参考文献へのリンクに変換する。"""
    def repl(m: re.Match) -> str:
        idx = int(m.group(1))
        if 0 <= idx < len(sources):
            return f'<a class="cite" href="#src-{idx}" title="{html.escape(sources[idx].title)}">[S{idx}]</a>'
        return m.group(0)

    return _CITATION.sub(repl, text_html)


_CSS = """
:root { --fg:#1a1a1a; --muted:#666; --accent:#2563eb; --border:#e5e7eb; --bg:#fff; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif;
  line-height: 1.8; color: var(--fg); background: var(--bg);
  max-width: 820px; margin: 0 auto; padding: 2.5rem 1.5rem; }
h1 { font-size: 1.9rem; border-bottom: 3px solid var(--accent); padding-bottom: .4rem; }
h2 { font-size: 1.35rem; margin-top: 2.2rem; border-left: 5px solid var(--accent);
  padding-left: .6rem; }
.meta { color: var(--muted); font-size: .9rem; }
.toc { background: #f8fafc; border: 1px solid var(--border); border-radius: 8px;
  padding: .8rem 1.4rem; }
.toc ol { margin: .3rem 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
a.cite { font-size: .8rem; vertical-align: super; color: var(--accent); }
.gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px; margin: 1.2rem 0; }
.gallery figure { margin: 0; border: 1px solid var(--border); border-radius: 8px;
  overflow: hidden; background: #f8fafc; }
.gallery img { width: 100%; height: 130px; object-fit: cover; display: block; }
.gallery figcaption { font-size: .72rem; color: var(--muted); padding: .35rem .5rem;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sources li { margin-bottom: .4rem; word-break: break-all; }
.sources .badge { display:inline-block; font-size:.7rem; color:#fff; background:var(--muted);
  border-radius: 4px; padding: 0 .35rem; margin-right: .4rem; }
footer { margin-top: 3rem; color: var(--muted); font-size: .8rem;
  border-top: 1px solid var(--border); padding-top: 1rem; }
"""


# ---- ブランド (tentekom.ai / 「てんてこ舞い」モチーフ) ----
# 慌ただしく回る「風車(ピンウィール)」= てんてこ舞い を表現したロゴ。

def _pinwheel_svg(size: int = 40, animate: bool = True) -> str:
    blade = '<path d="M32 32 V7 Q45 7 45 20 Z"/>'
    spin = ' class="ttk-spin"' if animate else ""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 64 64" '
        f'xmlns="http://www.w3.org/2000/svg" aria-label="tentekom.ai">'
        f'<g{spin} style="transform-origin:32px 32px">'
        f'<g fill="#2563eb">{blade}</g>'
        f'<g fill="#f59e0b" transform="rotate(90 32 32)">{blade}</g>'
        f'<g fill="#3b82f6" transform="rotate(180 32 32)">{blade}</g>'
        f'<g fill="#fbbf24" transform="rotate(270 32 32)">{blade}</g>'
        f'<circle cx="32" cy="32" r="5" fill="#1e293b"/>'
        f'<circle cx="32" cy="32" r="2" fill="#fff"/>'
        f'</g></svg>'
    )


def _favicon_link() -> str:
    from urllib.parse import quote
    svg = _pinwheel_svg(size=64, animate=False)
    return f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{quote(svg)}">'


def _brand_header() -> str:
    return (
        '<header class="brand">'
        f'<span class="logo">{_pinwheel_svg(40)}</span>'
        '<span class="wordmark">tentekom<span class="dot">.ai</span></span>'
        '<span class="tagline">てんてこ舞いを、自動で。</span>'
        '</header>'
    )


_BRAND_CSS = """
header.brand { display:flex; align-items:center; gap:.6rem; margin-bottom:1.2rem;
  padding-bottom:.8rem; border-bottom:1px solid var(--border); }
header.brand .logo { line-height:0; }
header.brand .wordmark { font-size:1.35rem; font-weight:800; letter-spacing:-.5px; color:#1e293b; }
header.brand .wordmark .dot { color:var(--accent); }
header.brand .tagline { font-size:.78rem; color:var(--muted); margin-left:auto; }
@keyframes ttk-spin { to { transform: rotate(360deg); } }
.ttk-spin { animation: ttk-spin 3.5s linear infinite; }
@media (prefers-reduced-motion: reduce) { .ttk-spin { animation: none; } }
"""


def build_news_markdown(
    title: str,
    categories: list[NewsCategory],
    keyword: str,
) -> str:
    today = _dt.date.today().isoformat()
    total = sum(len(c.items) for c in categories)
    lines = [f"# {title}", ""]
    lines.append(f"*キーワード: {keyword} / 生成日: {today} / 全 {total} 件*")
    lines.append("")
    lines.append("## 目次")
    for c in categories:
        lines.append(f"- {c.title}（{len(c.items)}件）")
    lines.append("")
    for c in categories:
        lines.append(f"## {c.title}")
        lines.append("")
        for it in c.items:
            date = f" ({it.published_at})" if it.published_at else ""
            lines.append(f"### {it.headline}")
            lines.append(f"{it.summary}")
            lines.append(f"出典: [{it.url}]({it.url}){date}")
            lines.append("")
    return "\n".join(lines)


def build_news_html(
    title: str,
    categories: list[NewsCategory],
    images: list[ImageRef],
    keyword: str,
) -> str:
    today = _dt.date.today().isoformat()
    total = sum(len(c.items) for c in categories)

    toc = "\n".join(
        f'<li><a href="#cat-{i}">{html.escape(c.title)}</a> '
        f'<span class="meta">({len(c.items)}件)</span></li>'
        for i, c in enumerate(categories)
    )

    gallery = ""
    if images:
        figs = "\n".join(
            f'<figure><a href="{html.escape(im.source_url or im.img_src)}" target="_blank" rel="noopener">'
            f'<img src="{html.escape(im.img_src)}" alt="{html.escape(im.title)}" loading="lazy" '
            f'onerror="this.closest(\'figure\').style.display=\'none\'"></a>'
            f'<figcaption>{html.escape(im.title)}</figcaption></figure>'
            for im in images
        )
        gallery = f'<div class="gallery">\n{figs}\n</div>'

    from urllib.parse import urlparse

    def _favicon(u: str) -> str:
        host = urlparse(u).hostname or ""
        return f"https://www.google.com/s2/favicons?domain={host}&sz=128" if host else ""

    sections = []
    for i, c in enumerate(categories):
        cards = []
        for it in c.items:
            host = urlparse(it.url).hostname or ""
            date = f'<span class="date">{html.escape(it.published_at)}</span>' if it.published_at else ""
            # サムネ: og:image があればそれ、無ければ favicon。読み込み失敗時も favicon へ。
            fav = _favicon(it.url)
            primary = it.image_url or fav
            thumb_cls = "thumb" if it.image_url else "thumb thumb-fav"
            thumb = (
                f'<a class="{thumb_cls}" href="{html.escape(it.url)}" target="_blank" rel="noopener">'
                f'<img src="{html.escape(primary)}" alt="" loading="lazy"'
                f' onerror="this.onerror=null;this.src=\'{html.escape(fav)}\';'
                f'this.closest(\'.thumb\').classList.add(\'thumb-fav\')"></a>'
            ) if primary else ""
            cards.append(
                '<article class="news">'
                f'{thumb}'
                '<div class="news-body">'
                f'<h3><a href="{html.escape(it.url)}" target="_blank" rel="noopener">{html.escape(it.headline)}</a></h3>'
                f'<p>{html.escape(it.summary)}</p>'
                f'<div class="src"><span class="host">{html.escape(host)}</span>{date}</div>'
                "</div>"
                "</article>"
            )
        sections.append(
            f'<section id="cat-{i}">\n<h2>{html.escape(c.title)} '
            f'<span class="count">{len(c.items)}件</span></h2>\n'
            + "\n".join(cards)
            + "\n</section>"
        )
    body_html = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
{_favicon_link()}
<style>{_CSS}{_BRAND_CSS}{_NEWS_CSS}</style>
</head>
<body>
{_brand_header()}
<h1>{html.escape(title)}</h1>
<p class="meta">キーワード: {html.escape(keyword)} ／ 生成日: {today} ／ 全 {total} 件</p>
{gallery}
<nav class="toc"><strong>カテゴリ</strong>
<ul>
{toc}
</ul>
</nav>
{body_html}
<footer>本ダイジェストはローカル LLM により自動生成されました。各ニュースは必ず出典元で確認してください。</footer>
</body>
</html>
"""


_NEWS_CSS = """
.toc ul { list-style: none; padding-left: 0; columns: 2; }
.count { font-size: .8rem; color: var(--muted); font-weight: normal; }
article.news { border: 1px solid var(--border); border-radius: 10px; padding: .7rem .9rem;
  margin: .7rem 0; background: #fff; display: flex; gap: .9rem; align-items: flex-start; }
article.news .thumb { flex: 0 0 132px; line-height: 0; }
article.news .thumb img { width: 132px; height: 88px; object-fit: cover; border-radius: 8px;
  background: #f1f5f9; }
/* favicon フォールバック時は小さく中央に */
article.news .thumb-fav { display:flex; align-items:center; justify-content:center;
  width:132px; height:88px; background:#f1f5f9; border-radius:8px; }
article.news .thumb-fav img { width:40px; height:40px; object-fit:contain; border-radius:0;
  background:transparent; }
article.news .news-body { flex: 1 1 auto; min-width: 0; }
article.news h3 { margin: 0 0 .35rem; font-size: 1.05rem; line-height: 1.4; }
article.news p { margin: 0 0 .5rem; }
article.news .src { font-size: .78rem; color: var(--muted); display: flex; gap: .8rem; }
article.news .host { background: #f1f5f9; border-radius: 4px; padding: 0 .4rem; }
@media (max-width: 560px) {
  article.news { flex-direction: column; }
  article.news .thumb, article.news .thumb img, article.news .thumb-fav { width: 100%; }
}
"""


def build_html(
    title: str,
    chapters: list[ChapterResult],
    sources: list[Source],
    images: list[ImageRef],
    keyword: str,
) -> str:
    today = _dt.date.today().isoformat()

    # 目次
    toc_items = "\n".join(
        f'<li><a href="#ch-{ch.index}">{html.escape(ch.title)}</a></li>'
        for ch in chapters
    )

    # 画像ギャラリー
    gallery = ""
    if images:
        figs = "\n".join(
            f'<figure><a href="{html.escape(im.source_url or im.img_src)}" target="_blank" rel="noopener">'
            f'<img src="{html.escape(im.img_src)}" alt="{html.escape(im.title)}" loading="lazy" '
            f'onerror="this.closest(\'figure\').style.display=\'none\'"></a>'
            f'<figcaption>{html.escape(im.title)}</figcaption></figure>'
            for im in images
        )
        gallery = f'<h2>関連画像</h2>\n<div class="gallery">\n{figs}\n</div>'

    # 本文(各章 Markdown -> HTML, 引用をリンク化)
    body_parts = []
    for ch in chapters:
        ch_html = md.markdown(ch.body, extensions=["extra", "nl2br"])
        ch_html = _linkify_citations(ch_html, sources)
        body_parts.append(
            f'<section id="ch-{ch.index}">\n<h2>{ch.index + 1}. {html.escape(ch.title)}</h2>\n{ch_html}\n</section>'
        )
    body_html = "\n".join(body_parts)

    # 参考文献
    sources_html = ""
    if sources:
        items = "\n".join(
            f'<li id="src-{i}"><span class="badge">S{i}</span>'
            f'<a href="{html.escape(s.url)}" target="_blank" rel="noopener">{html.escape(s.title)}</a>'
            + (f' <span class="meta">({html.escape(s.published_at)})</span>' if s.published_at else "")
            + "</li>"
            for i, s in enumerate(sources)
        )
        sources_html = f'<h2>参考文献</h2>\n<ol class="sources">\n{items}\n</ol>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
{_favicon_link()}
<style>{_CSS}{_BRAND_CSS}</style>
</head>
<body>
{_brand_header()}
<h1>{html.escape(title)}</h1>
<p class="meta">キーワード: {html.escape(keyword)} ／ 生成日: {today}</p>
<nav class="toc">
<strong>目次</strong>
<ol>
{toc_items}
</ol>
</nav>
{gallery}
{body_html}
{sources_html}
<footer>本レポートはローカル LLM により自動生成されました。内容は必ず原典で確認してください。</footer>
</body>
</html>
"""
