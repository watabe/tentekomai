"""News digest: 収集した多数のニュースをカテゴリ分類し、1件ずつ短く要約する。

essay 型(章ごとの長文)と異なり、具体的なニュースを多数列挙することを目的とする。
LLM 呼び出しを抑えるため、要約はバッチ(複数件まとめて1回)で行う。
"""

from __future__ import annotations

import json
import logging
import re

from ..llm import LLMClient
from ..models import NewsCategory, NewsItem, Source

logger = logging.getLogger(__name__)

# 出力切れで配列が途中まででも、完成している項目オブジェクトだけ救出する。
_ITEM_OBJ = re.compile(r"\{[^{}]*?\"index\"[^{}]*?\}", re.DOTALL)


def _salvage_items(text: str) -> list[dict]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    out: list[dict] = []
    for m in _ITEM_OBJ.findall(text):
        try:
            out.append(json.loads(m))
        except json.JSONDecodeError:
            continue
    return out

_CAT_SYSTEM = "あなたはニュース編集者です。出力は必ず指定の JSON のみ。"

_CAT_USER = """\
キーワード「{keyword}」に関するニュース一覧を、内容で{min_c}〜{max_c}個のカテゴリに分類します。
各ニュースの番号とタイトル:
{titles}

次の JSON のみを出力:
{{
  "categories": [
    {{"title": "カテゴリ名", "indices": [0, 3, 5]}}
  ]
}}
ルール:
- すべての番号をいずれかのカテゴリに必ず1回だけ割り当てる。
- カテゴリ名は具体的・簡潔に(例「新製品・発表」「AI・半導体」)。
- 日本語。
"""

_SUM_SYSTEM = (
    "あなたはニュース要約者です。各ニュースを、与えられた情報だけを使って"
    "2〜3文で要約します。情報にない事実は足しません。出力は JSON のみ。"
)

_SUM_USER = """\
次のニュースをそれぞれ要約してください。
各ニュース(番号 / タイトル / 抜粋):
{items}

次の JSON のみを出力:
{{
  "items": [
    {{"index": 0, "headline": "簡潔な見出し(20〜40字)", "summary": "2〜3文の要約"}}
  ]
}}
ルール:
- 入力の全番号について出力する。
- headline はタイトルを言い換えた具体的な見出し。
- summary は抜粋に書かれた内容のみ。推測や一般論で埋めない。
- {tone}
- 日本語。
"""

# audience 別の文体
_TONE = {
    "consumer": (
        "やや柔らかく親しみやすい丁寧語で書く。"
        "「〜と述べられています」「〜が示されています」のような硬い言い回しは避け、"
        "要点を生き生きと伝える。headline は具体的でキャッチーに。"
    ),
    "beginner": (
        "やさしく親しみやすい丁寧語で、専門用語は噛み砕いて書く。"
        "硬い言い回しは避ける。headline は分かりやすく。"
    ),
}
_DEFAULT_TONE = "事実を簡潔・中立に伝える。"


def categorize(
    llm: LLMClient,
    keyword: str,
    sources: list[Source],
    min_categories: int,
    max_categories: int,
    max_tokens: int,
) -> list[tuple[str, list[int]]]:
    titles = "\n".join(f"{i}. {s.title}" for i, s in enumerate(sources))
    user = _CAT_USER.format(
        keyword=keyword, titles=titles, min_c=min_categories, max_c=max_categories
    )
    data = llm.complete_json(_CAT_SYSTEM, user, max_tokens=max_tokens, tag="categorize")

    assigned: set[int] = set()
    cats: list[tuple[str, list[int]]] = []
    for c in data.get("categories", []):
        if not isinstance(c, dict):
            continue
        idxs = [
            j for j in c.get("indices", [])
            if isinstance(j, int) and 0 <= j < len(sources) and j not in assigned
        ]
        assigned.update(idxs)
        if idxs:
            cats.append((str(c.get("title", "その他")).strip() or "その他", idxs))

    # 未割り当ては「その他」へ
    leftover = [i for i in range(len(sources)) if i not in assigned]
    if leftover:
        cats.append(("その他", leftover))
    if not cats:
        cats = [("ニュース", list(range(len(sources))))]
    return cats


def summarize_items(
    llm: LLMClient,
    sources: list[Source],
    indices: list[int],
    batch_size: int,
    max_tokens: int,
    tone: str = "",
) -> dict[int, NewsItem]:
    """指定 index 群を batch_size 件ずつまとめて要約する。"""
    tone = tone or _DEFAULT_TONE
    result: dict[int, NewsItem] = {}
    for start in range(0, len(indices), batch_size):
        batch = indices[start : start + batch_size]
        items_block = "\n".join(
            f"{j}. {sources[j].title}\n   抜粋: {(sources[j].summary or '(抜粋なし)')[:300]}"
            for j in batch
        )
        user = _SUM_USER.format(items=items_block, tone=tone)
        # 出力切れに強くするため raw 取得 → 部分 JSON でも項目を救出する
        resp = llm.complete(
            _SUM_SYSTEM, user, max_tokens=max_tokens, temperature=0.1,
            tag=f"summarize_{start}",
        )
        items = _salvage_items(resp.text)
        if resp.truncated:
            logger.info("要約バッチ %d: 出力切れ。%d/%d 件を救出。",
                        start, len(items), len(batch))

        got = {}
        for it in items:
            j = it.get("index")
            if isinstance(j, int) and j in batch:
                got[j] = it

        for j in batch:
            s = sources[j]
            it = got.get(j, {})
            result[j] = NewsItem(
                source_index=j,
                headline=str(it.get("headline", "")).strip() or s.title,
                summary=str(it.get("summary", "")).strip() or (s.summary[:160] if s.summary else ""),
                url=s.url,
                published_at=s.published_at,
                reliability_score=s.reliability_score,
                image_url=s.image_url,
            )
    return result


def _group_by_category(sources: list[Source]) -> list[tuple[str, list[int]]]:
    """source.category(=検索クエリ)で分類。順序は初出順を維持。"""
    order: list[str] = []
    groups: dict[str, list[int]] = {}
    for i, s in enumerate(sources):
        key = s.category or "その他"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(i)
    return [(k, groups[k]) for k in order]


def build_digest(
    llm: LLMClient,
    keyword: str,
    sources: list[Source],
    min_categories: int,
    max_categories: int,
    batch_size: int,
    cat_max_tokens: int,
    sum_max_tokens: int,
    audience: str = "",
    use_llm_categorize: bool = False,
) -> list[NewsCategory]:
    # 既定では検索クエリをカテゴリに使う(LLM 分類呼び出し不要で安定)。
    if use_llm_categorize:
        cats = categorize(llm, keyword, sources, min_categories, max_categories, cat_max_tokens)
    else:
        cats = _group_by_category(sources)
    logger.info("[digest] %d カテゴリ", len(cats))

    # 全 index をまとめてバッチ要約(audience で文体を切替)
    tone = _TONE.get(audience, _DEFAULT_TONE)
    all_idx = [j for _, idxs in cats for j in idxs]
    summaries = summarize_items(llm, sources, all_idx, batch_size, sum_max_tokens, tone=tone)

    out: list[NewsCategory] = []
    for title, idxs in cats:
        items = [summaries[j] for j in idxs if j in summaries]
        items.sort(key=lambda x: x.reliability_score, reverse=True)
        if items:
            out.append(NewsCategory(title=title, items=items))
    return out
