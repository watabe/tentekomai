"""Planner: キーワードから調査方針と検索クエリを決める。"""

from __future__ import annotations

import logging
from typing import Any

from ..llm import LLMClient

logger = logging.getLogger(__name__)

_SYSTEM = "あなたはリサーチプランナーです。出力は必ず指定された JSON 形式のみ。"

_USER_TMPL = """\
キーワード「{keyword}」について{report_type}レポートを作成します。
読者層は{audience_label}、目標分量は約{length}字です。

このテーマを調べるための調査計画を立ててください。
出力は次の JSON のみ:

{{
  "intent": "このレポートで答えるべき問いの要約(1文)",
  "sub_questions": ["明らかにすべき小問1", "小問2", "..."],
  "search_queries": ["検索クエリ1", "検索クエリ2", "..."]
}}

制約:
- sub_questions は3〜6個。
- search_queries は{min_q}〜{max_q}個。キーワードを具体化した実用的な検索語にする。
  {query_guidance}
- 日本語で書く。
"""

# audience コードを和名に（プロンプトでの曖昧さを減らす）
_AUDIENCE_LABEL = {
    "business": "ビジネス",
    "engineer": "技術者",
    "beginner": "初心者",
    "consumer": "一般消費者",
}

# audience 別のクエリ生成方針
_CONSUMER_GUIDANCE = (
    "一般の消費者・ファンが知りたい具体的なニュースを狙う。"
    "キーワードの題材・分野に合った固有名詞(作品名/タイトル/ブランド名/製品名/"
    "キャラクター名/イベント名など)を使う。"
    "「新作」「発表」「発売」「リリース」「最新情報」「イベント」「コラボ」「レビュー」"
    "のような実利的な語を含める。"
    "キーワードと無関係な分野へ広げない"
    "(例: ガジェットが題材でないのにスマートウォッチ/家電/周辺機器の話に逸れない)。"
    "市場分析・投資・産業政策・業界レポート・B2B のような抽象的な切り口は避ける。"
)
_DEFAULT_GUIDANCE = (
    "幅広い切り口(製品/企業/技術/時期/分野 など)で多様なニュースが拾えるようにする。"
)
_QUERY_GUIDANCE = {"consumer": _CONSUMER_GUIDANCE}


def make_plan(
    llm: LLMClient,
    keyword: str,
    report_type: str,
    audience: str,
    length: int,
    max_tokens: int,
    min_queries: int = 3,
    max_queries: int = 6,
) -> dict[str, Any]:
    user = _USER_TMPL.format(
        keyword=keyword,
        report_type=report_type,
        audience_label=_AUDIENCE_LABEL.get(audience, audience),
        length=length,
        min_q=min_queries,
        max_q=max_queries,
        query_guidance=_QUERY_GUIDANCE.get(audience, _DEFAULT_GUIDANCE),
    )
    data = llm.complete_json(_SYSTEM, user, max_tokens=max_tokens, tag="plan")
    # 最低限の正規化
    plan = {
        "intent": str(data.get("intent", "")).strip(),
        "sub_questions": [str(s).strip() for s in data.get("sub_questions", []) if str(s).strip()],
        "search_queries": [str(s).strip() for s in data.get("search_queries", []) if str(s).strip()],
    }
    if not plan["search_queries"]:
        # フォールバック: キーワードそのものを使う
        plan["search_queries"] = [keyword]
    return plan
