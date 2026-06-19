"""LLM/検索サーバなしでパイプライン全体を検証するオフラインテスト。

FakeLLM がアウトライン/章本文/レビューの応答を返し、検索は無効化する。
実際のサーバ接続なしで plan->...->export まで通ることを確認する。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from report_agent.config import load_config  # noqa: E402
from report_agent.llm import LLMResponse, extract_json  # noqa: E402
from report_agent.pipeline import Pipeline  # noqa: E402
from report_agent.storage import Workspace  # noqa: E402


class FakeLLM:
    """LLMClient の代替。tag に応じて決め打ち応答を返す。"""

    def __init__(self, *a, **k):
        self.model = "fake"

    def ping(self):
        return True

    def complete_json(self, system, user, max_tokens=1024, tag=""):
        if tag == "plan":
            return {
                "intent": "ローカルLLMの比較",
                "sub_questions": ["速度は?", "精度は?"],
                "search_queries": ["ローカルLLM 比較", "llama.cpp 性能"],
            }
        if tag == "outline":
            return {
                "title": "ローカルLLM比較レポート",
                "chapters": [
                    {"title": "はじめに", "purpose": "背景", "points": ["目的"], "source_indices": []},
                    {"title": "比較", "purpose": "性能比較", "points": ["速度", "精度"], "source_indices": []},
                    {"title": "まとめ", "purpose": "結論", "points": ["総括"], "source_indices": []},
                ],
            }
        if tag.startswith("review"):
            return {"unsupported_claims": []}
        if tag == "categorize":
            return {"categories": [
                {"title": "新製品", "indices": [0]},
                {"title": "技術動向", "indices": [1]},
            ]}
        if tag.startswith("summarize"):
            # user に含まれる番号を雑に拾って返す
            import re
            idxs = sorted({int(n) for n in re.findall(r"^(\d+)\.", user, re.M)})
            return {"items": [
                {"index": j, "headline": f"見出し{j}", "summary": f"要約本文{j}"} for j in idxs
            ]}
        return {}

    def chat(self, messages, max_tokens=1024, temperature=None, tag=""):
        body = (
            "これはテスト用の章本文です。ローカルLLMは用途に応じて選定します。"
            "速度と精度のバランスが重要になります。結論として比較が有効です。"
        )
        return LLMResponse(text=body, finish_reason="stop")

    def complete(self, *a, **k):
        return LLMResponse(text="ok", finish_reason="stop")


def test_pipeline_end_to_end():
    cfg = load_config(None, overrides={
        "llm": {"provider": "llama.cpp", "model": "fake"},
        "search": {"enabled": False},
        "report": {"length": 3000},
    })
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace.create(tmp, "ローカルLLM 比較")
        pipe = Pipeline(ws, cfg, "ローカルLLM 比較")
        pipe.llm = FakeLLM()  # 差し替え
        outputs = pipe.run(fmt="all")

        assert (ws.root / "output" / "report.html").exists(), "HTML が生成されていない"
        assert (ws.root / "output" / "report.md").exists(), "MD が生成されていない"
        html = (ws.root / "output" / "report.html").read_text(encoding="utf-8")
        assert "ローカルLLM比較レポート" in html
        assert "はじめに" in html
        # state が全完了
        state = ws.load_state()
        for step in ["plan", "research", "outline", "write", "review", "export"]:
            assert step in state["completed"], f"{step} 未完了"
        # 章ファイル
        assert len(list(ws.chapters_dir.glob("*.md"))) == 3
        print("OK: パイプライン全体が通過。出力:", outputs)


def test_recency():
    from datetime import date
    from report_agent import recency

    today = date(2026, 6, 19)
    days = recency.window_days("day")  # 2
    # タイトル/URL から日付抽出
    assert recency.extract_date(title="今日のガジェット（2026年6月18日）") == date(2026, 6, 18)
    assert recency.extract_date(url="https://x.com/2026/06/18/foo") == date(2026, 6, 18)
    # 分類
    assert recency.classify(date(2026, 6, 18), days, today) == "fresh"   # 昨日
    assert recency.classify(date(2026, 3, 1), days, today) == "old"      # 春は除外
    assert recency.classify(None, days, today) == "unknown"
    assert recency.classify(date(2026, 8, 1), days, today) == "unknown"  # 未来(イベント日)
    # window_days
    assert recency.window_days("") == 0 and recency.window_days("month") == 32
    print("OK: 鮮度判定(日付抽出・分類)")


def test_news_reliability():
    from report_agent.search import _reliability
    # news モード: 報道メディア > 既定 > 政府/古いPDF
    media = _reliability("https://www.itmedia.co.jp/news/x.html", news=True)
    default = _reliability("https://example.com/x.html", news=True)
    gov = _reliability("https://www.env.go.jp/x.html", news=True)
    pdf = _reliability("https://www.env.go.jp/report.pdf", news=True)
    assert media > default > gov >= pdf, (media, default, gov, pdf)
    # 非 news は従来通り政府が最上位
    assert _reliability("https://www.env.go.jp/x.pdf", news=False) == 0.9
    print("OK: news モードの信頼度(報道>既定>政府≥PDF)")


def test_extract_json():
    assert extract_json('```json\n{"a":1}\n```') == {"a": 1}
    assert extract_json('前置き {"a": 2} 後置き') == {"a": 2}
    assert extract_json('[1,2,3]') == [1, 2, 3]
    print("OK: extract_json")


def test_news_digest():
    from report_agent.agents import digest
    from report_agent.exporter import build_news_html, build_news_markdown
    from report_agent.models import ImageRef, Source

    sources = [
        Source(title=f"ニュース{i}", url=f"https://example.com/{i}",
               summary=f"これはニュース{i}の抜粋です。", reliability_score=0.5 + i * 0.05)
        for i in range(5)
    ]
    cats = digest.build_digest(
        FakeLLM(), keyword="ガジェット", sources=sources,
        min_categories=2, max_categories=4, batch_size=3,
        cat_max_tokens=500, sum_max_tokens=800,
    )
    total = sum(len(c.items) for c in cats)
    assert total == 5, f"全ニュースが項目化されていない: {total}"
    # 各項目に見出し・要約・URL がある
    for c in cats:
        for it in c.items:
            assert it.headline and it.summary and it.url

    images = [ImageRef(title="img", img_src="https://example.com/a.jpg")]
    html = build_news_html("テストダイジェスト", cats, images, "ガジェット")
    assert "テストダイジェスト" in html and "全 5 件" in html
    assert html.count('article class="news"') == 5
    md = build_news_markdown("テストダイジェスト", cats, "ガジェット")
    assert "出典:" in md
    print("OK: ニュースダイジェスト(分類→要約→HTML/MD)")


def test_resume_skips_completed():
    cfg = load_config(None, overrides={
        "llm": {"provider": "llama.cpp", "model": "fake"},
        "search": {"enabled": False},
    })
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace.create(tmp, "再開テスト")
        pipe = Pipeline(ws, cfg, "再開テスト")
        pipe.llm = FakeLLM()
        pipe.run(fmt="md")
        # 2回目: 全ステップ完了済みなので LLM を壊しても通る
        pipe2 = Pipeline(ws, cfg, "再開テスト")
        pipe2.llm = None  # 呼ばれたら例外になる
        outputs = pipe2.run(fmt="md")
        assert outputs
        print("OK: resume はスキップして再実行できる")


if __name__ == "__main__":
    test_recency()
    test_news_reliability()
    test_extract_json()
    test_pipeline_end_to_end()
    test_news_digest()
    test_resume_skips_completed()
    print("\nすべてのオフラインテストに合格しました。")
