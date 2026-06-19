"""Reviewer: 章ごとの品質チェック。

要件定義 3-D に対応。出力切れ検知を最重要視する。
チェックは Python 側で機械的に行い、必要に応じて LLM 修正を呼ぶ。
"""

from __future__ import annotations

import logging
import re

from ..llm import LLMClient
from ..models import ChapterPlan, ChapterResult, Source

logger = logging.getLogger(__name__)

_CITATION = re.compile(r"\[S\d+\]")
_TRUNCATION_TAILS = ("、", "(", "「", "・", "及び", "および", "そして", "を", "が", "は")


def check_chapter(
    chapter: ChapterResult,
    plan: ChapterPlan,
    target_chars: int,
) -> list[str]:
    issues: list[str] = []
    body = chapter.body.strip()

    # 1) 出力切れ検知(最重要)
    if chapter.truncated:
        issues.append("出力切れ: finish_reason=length。本文が途中で切れています。")
    if body and body[-1] not in "。.!?！？」』）)":
        issues.append("出力切れ疑い: 文末が句点で終わっていません。")
    if body.endswith(_TRUNCATION_TAILS):
        issues.append("出力切れ疑い: 文末が接続表現で途切れています。")

    # 2) 文字数チェック
    if target_chars > 0:
        ratio = chapter.char_count / target_chars
        if ratio < 0.5:
            issues.append(f"文字数不足: {chapter.char_count}字 (目標{target_chars}字の半分未満)。")
        elif ratio > 2.0:
            issues.append(f"文字数超過: {chapter.char_count}字 (目標{target_chars}字の2倍超)。")

    # 3) 根拠チェック(素材が割り当てられているのに引用が無い)
    if plan.source_indices and not _CITATION.search(body):
        issues.append("根拠不足: 割り当て素材があるのに [S番号] 引用がありません。")

    # 4) 重複チェック(同一文の繰り返し)
    sentences = [s.strip() for s in re.split(r"[。\n]", body) if len(s.strip()) > 15]
    seen: dict[str, int] = {}
    for s in sentences:
        seen[s] = seen.get(s, 0) + 1
    dups = [s for s, c in seen.items() if c >= 2]
    if dups:
        issues.append(f"重複: 同一文の繰り返しが{len(dups)}件あります。")

    return issues


def llm_grounding_review(
    llm: LLMClient,
    chapter: ChapterResult,
    sources: list[Source],
    plan: ChapterPlan,
    max_tokens: int,
) -> list[str]:
    """LLM による事実性レビュー(任意)。素材と本文の整合を見る。"""
    if not plan.source_indices:
        return []
    materials = "\n".join(
        f"[S{j}] {sources[j].title}: {sources[j].summary[:300]}"
        for j in plan.source_indices
    )
    system = "あなたは事実確認の校閲者です。出力は JSON のみ。"
    user = f"""\
以下の本文が、与えた素材に書かれていない事実(数値・固有名詞・日付)を含んでいないか確認してください。

素材:
{materials}

本文:
{chapter.body[:1500]}

出力 JSON:
{{"unsupported_claims": ["素材に無いと思われる記述", "..."]}}
素材で裏付けできるものは挙げないでください。問題なければ空配列。
"""
    try:
        data = llm.complete_json(system, user, max_tokens=max_tokens, tag=f"review_ch{plan.index+1}")
        claims = [str(c).strip() for c in data.get("unsupported_claims", []) if str(c).strip()]
        return [f"事実性要確認: {c}" for c in claims[:5]]
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM 事実性レビューに失敗(継続): %s", e)
        return []
