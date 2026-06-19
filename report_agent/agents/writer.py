"""Writer: 章ごとに本文を生成する。

要件定義の方針: 1回の出力を大きくしすぎない / 役割を章本文生成のみに固定 /
出典にない事実を書かせない。
"""

from __future__ import annotations

import logging

from ..llm import LLMClient
from ..models import ChapterPlan, ChapterResult, Source

logger = logging.getLogger(__name__)

_SYSTEM = (
    "あなたはレポートライターです。与えられた素材とアウトラインだけを使い、"
    "担当の1章のみを書きます。出典にない事実・数値・固有名詞は書きません。"
)

_USER_TMPL = """\
レポートタイトル: {report_title}
あなたの担当は「第{n}章: {chapter_title}」の本文のみです。

章の目的: {purpose}
盛り込む論点:
{points}

利用してよい素材(これ以外の事実は書かない):
{materials}

執筆ルール:
- 約{target}字(±2割)で書く。
- 見出し記号(#)は付けない。本文だけを書く。
- 箇条書きは必要な箇所のみ。基本は段落で書く。
- 素材から事実を使ったら、文末に [S{first}] のように素材番号を付ける。
- 素材が無い場合は、一般的・概念的な説明にとどめ、具体的数値は書かない。
- 日本語で書く。前置きや「以下に述べます」等のメタ発言は不要。
"""


# 1章に渡す素材数の上限。多すぎると思考型モデルが思考過多になり遅延・出力切れを招く。
_MAX_MATERIALS_PER_CHAPTER = 4


def _materials_block(plan: ChapterPlan, sources: list[Source]) -> str:
    if not plan.source_indices:
        return "(この章に割り当てられた素材はありません)"
    lines = []
    for j in plan.source_indices[:_MAX_MATERIALS_PER_CHAPTER]:
        s = sources[j]
        snippet = s.summary[:250] if s.summary else "(要約なし)"
        lines.append(f"[S{j}] {s.title}\n  内容: {snippet}")
    return "\n".join(lines)


def write_chapter(
    llm: LLMClient,
    report_title: str,
    plan: ChapterPlan,
    sources: list[Source],
    target_chars: int,
    max_tokens: int,
    max_attempts: int = 3,
) -> ChapterResult:
    points = "\n".join(f"- {p}" for p in plan.points) or "- (指定なし)"
    materials = _materials_block(plan, sources)
    first_idx = plan.source_indices[0] if plan.source_indices else 0
    user = _USER_TMPL.format(
        report_title=report_title,
        n=plan.index + 1,
        chapter_title=plan.title,
        purpose=plan.purpose or "(指定なし)",
        points=points,
        materials=materials,
        target=target_chars,
        first=first_idx,
    )

    body = ""
    truncated = False
    attempts = 0
    # 出力切れ(finish_reason=length)時は続きを継続生成する
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
    while attempts < max_attempts:
        attempts += 1
        resp = llm.chat(messages, max_tokens=max_tokens, temperature=None,
                        tag=f"write_ch{plan.index+1}")
        body += resp.text
        truncated = resp.truncated
        if not truncated:
            break
        logger.warning("第%d章が出力切れ。続きを生成します(attempt=%d)。",
                       plan.index + 1, attempts)
        messages = messages + [
            {"role": "assistant", "content": resp.text},
            {"role": "user", "content": "続きを書いてください。重複は避けてください。"},
        ]

    body = body.strip()
    return ChapterResult(
        index=plan.index,
        title=plan.title,
        body=body,
        char_count=len(body),
        truncated=truncated,
        attempts=attempts,
    )
