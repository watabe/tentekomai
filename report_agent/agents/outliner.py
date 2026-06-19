"""Outliner: キーワードと収集済み素材からレポート構成を作る。"""

from __future__ import annotations

import logging

from ..llm import LLMClient
from ..models import ChapterPlan, Source

logger = logging.getLogger(__name__)

_SYSTEM = "あなたはレポート編集者です。出力は必ず指定された JSON 形式のみ。"

_USER_TMPL = """\
キーワード「{keyword}」の{report_type}レポートの構成(アウトライン)を作ります。
読者層は{audience}、目標分量は約{length}字です。

調査の意図: {intent}

利用可能な素材(番号 + タイトル):
{source_list}

次の JSON のみを出力してください:

{{
  "title": "レポート全体のタイトル",
  "chapters": [
    {{
      "title": "章タイトル",
      "purpose": "この章で書くことの要約(1文)",
      "points": ["盛り込む論点1", "論点2"],
      "source_indices": [0, 2]
    }}
  ]
}}

制約:
- chapters は{min_ch}〜{max_ch}章。最初を導入、最後をまとめにする。
- source_indices は上の素材番号(0始まり)から、その章に関係するものだけを選ぶ。素材が無ければ空配列。
- 日本語で書く。
"""


def make_outline(
    llm: LLMClient,
    keyword: str,
    report_type: str,
    audience: str,
    length: int,
    intent: str,
    sources: list[Source],
    chapter_target_chars: int,
    max_tokens: int,
) -> tuple[str, list[ChapterPlan]]:
    # 目標分量から章数の目安を決める
    target_chapters = max(3, min(8, round(length / max(chapter_target_chars, 1))))
    min_ch = max(3, target_chapters - 1)
    max_ch = min(8, target_chapters + 1)

    source_list = "\n".join(
        f"{i}. {s.title}" for i, s in enumerate(sources)
    ) or "(素材なし。一般知識で構成してください)"

    user = _USER_TMPL.format(
        keyword=keyword,
        report_type=report_type,
        audience=audience,
        length=length,
        intent=intent or keyword,
        source_list=source_list,
        min_ch=min_ch,
        max_ch=max_ch,
    )
    data = llm.complete_json(_SYSTEM, user, max_tokens=max_tokens, tag="outline")

    title = str(data.get("title", "")).strip() or keyword
    chapters: list[ChapterPlan] = []
    for i, ch in enumerate(data.get("chapters", [])):
        if not isinstance(ch, dict):
            continue
        valid_idx = [
            j for j in ch.get("source_indices", [])
            if isinstance(j, int) and 0 <= j < len(sources)
        ]
        chapters.append(
            ChapterPlan(
                index=i,
                title=str(ch.get("title", f"第{i+1}章")).strip(),
                purpose=str(ch.get("purpose", "")).strip(),
                points=[str(p).strip() for p in ch.get("points", []) if str(p).strip()],
                source_indices=valid_idx,
            )
        )
    if not chapters:
        # フォールバック構成
        chapters = [
            ChapterPlan(index=0, title="はじめに", purpose="背景と目的"),
            ChapterPlan(index=1, title="本論", purpose="主要な論点",
                        source_indices=list(range(len(sources)))),
            ChapterPlan(index=2, title="まとめ", purpose="結論と展望"),
        ]
    return title, chapters
