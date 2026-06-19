"""パイプライン本体。各ステップを順に実行し、中間成果物を保存する。

ステップ: plan -> research -> outline -> write -> review -> export
state.json に完了ステップを記録し、resume 時は未完ステップから再開する。
"""

from __future__ import annotations

import logging

from .agents import digest, outliner, planner, reviewer, writer
from .call_log import CallLogger
from .config import Config
from .exporter import (
    build_html,
    build_markdown,
    build_news_html,
    build_news_markdown,
)
from .llm import LLMClient
from .models import ChapterPlan, ChapterResult, ImageRef, NewsCategory, Source
from .search import SearXNGClient
from .storage import Workspace
from .thumbnails import enrich_sources, enrich_thumbnails

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, ws: Workspace, config: Config, keyword: str):
        self.ws = ws
        self.cfg = config
        self.keyword = keyword
        self.call_log = CallLogger(ws.llm_log_path)
        self.llm = LLMClient(
            base_url=config.base_url,
            model=config.llm["model"],
            api_key=config.llm.get("api_key", "not-needed"),
            temperature=config.llm.get("temperature", 0.3),
            timeout_sec=config.llm.get("timeout_sec", 300),
            max_retries=config.llm.get("max_retries", 3),
            call_log=self.call_log,
        )

    @property
    def is_news(self) -> bool:
        """news 型はダイジェスト(多数のニュース列挙)パスを使う。"""
        return self.cfg.report.get("type") == "news"

    def effective_time_range(self) -> str:
        """実効 time_range。明示指定(""=オフ含む)があれば優先、なければ news は既定窓。"""
        explicit = self.cfg.search.get("time_range")
        if explicit is not None:
            return explicit
        return self.cfg.search.get("news_time_range", "month") if self.is_news else ""

    # ---- 各ステップ ----

    def step_plan(self) -> dict:
        if self.ws.is_done("plan"):
            logger.info("[plan] スキップ(完了済み)")
            return self.ws.read_json(self.ws.plan_path)
        logger.info("[plan] 調査計画を作成中...")
        r = self.cfg.report
        # news ダイジェストはクエリ数を増やして多様なニュースを集める
        if self.is_news:
            nq = self.cfg.search.get("news_queries", 9)
            min_q, max_q = max(5, nq - 2), nq
        else:
            min_q, max_q = 3, 6
        plan = planner.make_plan(
            self.llm,
            keyword=self.keyword,
            report_type=r["type"],
            audience=r["audience"],
            length=r["length"],
            max_tokens=self.cfg.generation["outline_max_tokens"],
            min_queries=min_q,
            max_queries=max_q,
        )
        self.ws.write_json(self.ws.plan_path, plan)
        self.ws.mark_done("plan")
        logger.info("[plan] 検索クエリ %d 件", len(plan["search_queries"]))
        return plan

    def step_research(self, plan: dict) -> tuple[list[Source], list[ImageRef]]:
        if self.ws.is_done("research"):
            logger.info("[research] スキップ(完了済み)")
            return self._load_sources(), self._load_images()

        sources: list[Source] = []
        images: list[ImageRef] = []

        if not self.cfg.search.get("enabled", True):
            logger.info("[research] 検索無効。素材なしで進めます。")
        else:
            max_results = (
                self.cfg.search.get("news_max_results", 12)
                if self.is_news
                else self.cfg.search["max_results"]
            )
            time_range = self.effective_time_range()
            sc = SearXNGClient(
                base_url=self.cfg.search["searxng_url"],
                max_results=max_results,
                max_images=self.cfg.search["max_images"],
                language=self.cfg.search.get("language", "ja"),
                timeout_sec=self.cfg.search.get("timeout_sec", 30),
                time_range=time_range,
                news_mode=self.is_news,
            )
            if time_range:
                logger.info("[research] 鮮度フィルタ time_range=%s", time_range)
            seen_urls: set[str] = set()
            seen_imgs: set[str] = set()
            for q in plan["search_queries"]:
                logger.info("[research] 検索: %s", q)
                for s in sc.search_text(q):
                    if s.url not in seen_urls:
                        seen_urls.add(s.url)
                        s.category = q  # 見つけたクエリをカテゴリとして記録
                        sources.append(s)
                for im in sc.search_images(q):
                    if im.img_src not in seen_imgs:
                        seen_imgs.add(im.img_src)
                        images.append(im)
            # 信頼度順に並べる
            sources.sort(key=lambda s: s.reliability_score, reverse=True)
            images = images[: self.cfg.search["max_images"]]

        self.ws.write_json(self.ws.sources_path, [s.to_dict() for s in sources])
        self.ws.write_json(self.ws.images_path, [im.to_dict() for im in images])
        self.ws.mark_done("research")
        logger.info("[research] 素材 %d 件 / 画像 %d 件", len(sources), len(images))
        return sources, images

    def step_outline(self, plan: dict, sources: list[Source]) -> tuple[str, list[ChapterPlan]]:
        if self.ws.is_done("outline"):
            logger.info("[outline] スキップ(完了済み)")
            data = self.ws.read_json(self.ws.outline_path)
            return data["title"], [ChapterPlan.from_dict(c) for c in data["chapters"]]
        logger.info("[outline] アウトライン作成中...")
        r = self.cfg.report
        title, chapters = outliner.make_outline(
            self.llm,
            keyword=self.keyword,
            report_type=r["type"],
            audience=r["audience"],
            length=r["length"],
            intent=plan.get("intent", ""),
            sources=sources,
            chapter_target_chars=self.cfg.generation["chapter_target_chars"],
            max_tokens=self.cfg.generation["outline_max_tokens"],
        )
        if r.get("title"):
            title = r["title"]
        self.ws.write_json(
            self.ws.outline_path,
            {"title": title, "chapters": [c.to_dict() for c in chapters]},
        )
        self.ws.mark_done("outline")
        logger.info("[outline] %s (%d章)", title, len(chapters))
        return title, chapters

    def step_write(
        self, title: str, chapters: list[ChapterPlan], sources: list[Source]
    ) -> list[ChapterResult]:
        results: list[ChapterResult] = []
        target = self.cfg.generation["chapter_target_chars"]
        max_tokens = self.cfg.generation["chapter_max_tokens"]

        for plan in chapters:
            out_path = self.ws.chapters_dir / f"{plan.index:02d}_{_safe(plan.title)}.md"
            meta_path = self.ws.chapters_dir / f"{plan.index:02d}.json"
            # 章単位 resume: 既存なら再利用
            if meta_path.exists():
                logger.info("[write] 第%d章 スキップ(生成済み)", plan.index + 1)
                results.append(ChapterResult.from_dict(self.ws.read_json(meta_path)))
                continue
            logger.info("[write] 第%d章「%s」生成中...", plan.index + 1, plan.title)
            res = writer.write_chapter(
                self.llm, title, plan, sources,
                target_chars=target, max_tokens=max_tokens,
            )
            out_path.write_text(f"## {plan.index+1}. {plan.title}\n\n{res.body}\n", encoding="utf-8")
            self.ws.write_json(meta_path, res.to_dict())
            results.append(res)
        self.ws.mark_done("write")
        return results

    def step_review(
        self, chapters: list[ChapterPlan], results: list[ChapterResult], sources: list[Source]
    ) -> dict:
        logger.info("[review] 品質チェック中...")
        target = self.cfg.generation["chapter_target_chars"]
        review_tokens = self.cfg.generation["review_max_tokens"]
        plan_by_idx = {c.index: c for c in chapters}
        report: dict = {"chapters": [], "total_issues": 0}

        for res in results:
            plan = plan_by_idx.get(res.index)
            if plan is None:
                continue
            issues = reviewer.check_chapter(res, plan, target)
            issues += reviewer.llm_grounding_review(
                self.llm, res, sources, plan, max_tokens=review_tokens
            )
            res.issues = issues
            report["chapters"].append(
                {"index": res.index, "title": res.title,
                 "char_count": res.char_count, "issues": issues}
            )
            report["total_issues"] += len(issues)
            # メタを更新(issues 反映)
            self.ws.write_json(self.ws.chapters_dir / f"{res.index:02d}.json", res.to_dict())
            if issues:
                logger.warning("[review] 第%d章: %d件の指摘", res.index + 1, len(issues))

        self.ws.write_json(self.ws.issues_path, report)
        self.ws.mark_done("review")
        logger.info("[review] 指摘 合計 %d 件", report["total_issues"])
        return report

    def step_export(
        self,
        title: str,
        results: list[ChapterResult],
        sources: list[Source],
        images: list[ImageRef],
        fmt: str,
    ) -> list[str]:
        logger.info("[export] 出力生成中 (%s)...", fmt)
        out_dir = self.ws.root / "output"
        written: list[str] = []

        md_text = build_markdown(title, results, sources, self.keyword)
        md_path = out_dir / "report.md"
        md_path.write_text(md_text, encoding="utf-8")
        written.append(str(md_path))

        if fmt in ("html", "pdf", "all"):
            html_text = build_html(title, results, sources, images, self.keyword)
            if fmt in ("html", "all"):
                html_path = out_dir / "report.html"
                html_path.write_text(html_text, encoding="utf-8")
                written.append(str(html_path))
            if fmt in ("pdf", "all"):
                written.append(self._write_pdf(html_text, out_dir))

        self.ws.mark_done("export")
        return written

    # ---- news ダイジェスト ----

    @property
    def digest_path(self):
        return self.ws.root / "digest.json"

    def _apply_recency(self, sources: list[Source]) -> list[Source]:
        """time_range が有効なとき、タイトル/URL の日付で鮮度優先に並べ替える。

        - fresh(窓内の日付): 新しい順に先頭へ。published_at も埋める。
        - old(窓より古い日付): 除外。
        - unknown(日付不明/未来日): fresh の後ろにバックフィル(信頼度順は維持)。
        time_range が無効(空)なら元の順序(信頼度順)のまま。
        """
        from . import recency

        days = recency.window_days(self.effective_time_range())
        if days <= 0:
            return sources

        fresh: list[tuple] = []
        unknown: list[Source] = []
        old = 0
        for s in sources:
            d = recency.extract_date(title=s.title, url=s.url, meta=s.published_at)
            if d is not None:
                s.published_at = d.isoformat()  # 表示用に日付だけに正規化
            kind = recency.classify(d, days)
            if kind == "fresh":
                fresh.append((d, s))
            elif kind == "old":
                old += 1
            else:
                unknown.append(s)
        fresh.sort(key=lambda t: t[0], reverse=True)
        fresh_sources = [s for _, s in fresh]
        # 鮮度優先: 日付の新しい記事が十分あれば、それだけを使う(件数が少なくても可)。
        # 少なすぎるとき(空回避)のみ、日付不明の記事でバックフィルする。
        min_fresh = 5
        if len(fresh_sources) >= min_fresh:
            result = fresh_sources
            note = "新しい日付付きのみ採用"
        else:
            result = fresh_sources + unknown
            note = "新しい記事が少ないため日付不明もバックフィル"
        logger.info(
            "[digest] 鮮度: 新しい %d 件 / 不明 %d 件 / 古い(除外) %d 件 → %s",
            len(fresh_sources), len(unknown), old, note,
        )
        return result

    def step_digest(self, sources: list[Source]) -> tuple[str, list[NewsCategory]]:
        title = self.cfg.report.get("title") or f"{self.keyword} ニュースダイジェスト"
        if self.ws.is_done("digest"):
            logger.info("[digest] スキップ(完了済み)")
            data = self.ws.read_json(self.digest_path)
            cats = [NewsCategory.from_dict(c) for c in data["categories"]]
            return data.get("title", title), cats

        g = self.cfg.generation
        cap = g.get("max_news_items", 45)
        from . import recency
        days = recency.window_days(self.effective_time_range())

        if days > 0:
            # 鮮度優先: 候補ページを取得して公開日時(meta)＋サムネを埋め、
            # 日付で新しい順に並べ替え・古い記事を除外してから上限トリム。
            candidate = sources[: max(cap, 60)]
            enrich_sources(candidate, want_date=True)
            ordered = self._apply_recency(candidate)
            digest_sources = ordered[:cap]
        else:
            digest_sources = sources[:cap]
            enrich_thumbnails(digest_sources)
        logger.info(
            "[digest] %d 件中 %d 件を分類・要約中...",
            len(sources), len(digest_sources),
        )
        cats = digest.build_digest(
            self.llm,
            keyword=self.keyword,
            audience=self.cfg.report.get("audience", ""),
            sources=digest_sources,
            min_categories=3,
            max_categories=g.get("max_news_categories", 6),
            batch_size=g.get("summary_batch_size", 8),
            cat_max_tokens=g.get("digest_cat_max_tokens", 1200),
            sum_max_tokens=g.get("digest_sum_max_tokens", 2500),
        )
        self.ws.write_json(
            self.digest_path,
            {"title": title, "categories": [c.to_dict() for c in cats]},
        )
        self.ws.mark_done("digest")
        total = sum(len(c.items) for c in cats)
        logger.info("[digest] %d カテゴリ / 計 %d 件のニュース", len(cats), total)
        return title, cats

    def step_export_news(
        self, title: str, cats: list[NewsCategory], images: list[ImageRef], fmt: str
    ) -> list[str]:
        logger.info("[export] ニュースダイジェスト出力中 (%s)...", fmt)
        out_dir = self.ws.root / "output"
        written: list[str] = []

        md_text = build_news_markdown(title, cats, self.keyword)
        md_path = out_dir / "report.md"
        md_path.write_text(md_text, encoding="utf-8")
        written.append(str(md_path))

        if fmt in ("html", "pdf", "all"):
            html_text = build_news_html(title, cats, images, self.keyword)
            if fmt in ("html", "all"):
                html_path = out_dir / "report.html"
                html_path.write_text(html_text, encoding="utf-8")
                written.append(str(html_path))
            if fmt in ("pdf", "all"):
                written.append(self._write_pdf(html_text, out_dir))

        self.ws.mark_done("export")
        return written

    def _write_pdf(self, html_text: str, out_dir) -> str:
        """HTML を スマホ向け PDF(画像埋め込み) に変換。失敗しても止めない。"""
        from .pdf import html_to_pdf
        pdf_path = out_dir / "report.pdf"
        try:
            html_to_pdf(html_text, str(pdf_path))
            logger.info("[export] PDF 出力: %s", pdf_path)
            return str(pdf_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("PDF 生成に失敗しました(HTML/MD は出力済み): %s", e)
            return f"(PDF失敗: {e})"

    # ---- 実行 ----

    def run(self, fmt: str = "html") -> list[str]:
        plan = self.step_plan()
        sources, images = self.step_research(plan)
        if self.is_news:
            # ニュースダイジェスト: 多数のニュースを分類して列挙
            title, cats = self.step_digest(sources)
            return self.step_export_news(title, cats, images, fmt)
        title, chapters = self.step_outline(plan, sources)
        results = self.step_write(title, chapters, sources)
        self.step_review(chapters, results, sources)
        return self.step_export(title, results, sources, images, fmt)

    # ---- ロード補助 ----

    def _load_sources(self) -> list[Source]:
        if self.ws.sources_path.exists():
            return [Source.from_dict(d) for d in self.ws.read_json(self.ws.sources_path)]
        return []

    def _load_images(self) -> list[ImageRef]:
        if self.ws.images_path.exists():
            return [ImageRef.from_dict(d) for d in self.ws.read_json(self.ws.images_path)]
        return []


def _safe(name: str, n: int = 24) -> str:
    import re
    s = re.sub(r"[^\w぀-ヿ一-鿿]+", "_", name).strip("_")
    return s[:n] or "chapter"
