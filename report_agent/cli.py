"""CLI エントリポイント。

使用例:
  report-agent run "ローカルLLM エージェント 比較" --provider llama.cpp \\
      --type research --length 5000 --searxng-url http://localhost:8888
  report-agent resume reports/20260616_xxxx
  report-agent providers
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import PROVIDER_PRESETS, load_config
from .llm import LLMClient, LLMError
from .pipeline import Pipeline
from .search import SearchError
from .storage import Workspace


def _setup_logging(verbose: bool):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_overrides(args) -> dict:
    """CLI 引数を config オーバーライド辞書へ。None は無視される。"""
    return {
        "llm": {
            "provider": getattr(args, "provider", None),
            "base_url": getattr(args, "base_url", None),
            "model": getattr(args, "model", None),
            "temperature": getattr(args, "temperature", None),
        },
        "search": {
            "searxng_url": getattr(args, "searxng_url", None),
            "enabled": (False if getattr(args, "no_search", False) else None),
            # off→"" (明示オフ) / 未指定→None(既定ロジック)
            "time_range": (
                None if getattr(args, "time_range", None) is None
                else ("" if args.time_range == "off" else args.time_range)
            ),
        },
        "report": {
            "type": getattr(args, "type", None),
            "audience": getattr(args, "audience", None),
            "length": getattr(args, "length", None),
            "title": getattr(args, "title", None),
        },
    }


def cmd_run(args) -> int:
    overrides = _build_overrides(args)
    cfg = load_config(args.config, overrides)

    if not cfg.base_url:
        print(
            "エラー: LLM の接続先が未確定です。--provider (llama.cpp/lmstudio) か "
            "--base-url を指定してください。`report-agent providers` で一覧表示。",
            file=sys.stderr,
        )
        return 2

    # 接続確認
    probe = LLMClient(cfg.base_url, cfg.llm["model"], cfg.llm.get("api_key", "not-needed"))
    print(f"LLM 接続先: {cfg.base_url} (model={cfg.llm['model']})")
    if not probe.ping():
        print(
            f"警告: {cfg.base_url} に到達できません。サーバが起動しているか確認してください。",
            file=sys.stderr,
        )
        if not args.force:
            print("中止します(--force で無視して続行できます)。", file=sys.stderr)
            return 3

    ws = Workspace.create(args.out, args.keyword)
    state = ws.load_state()
    state["keyword"] = args.keyword
    ws.save_state(state)
    ws.write_yaml(ws.input_path, {"keyword": args.keyword, "config": cfg.to_dict(), "format": args.format})

    print(f"作業ディレクトリ: {ws.root}")
    pipe = Pipeline(ws, cfg, args.keyword)
    try:
        outputs = pipe.run(fmt=args.format)
    except KeyboardInterrupt:
        print("\n中断しました。`report-agent resume %s` で再開できます。" % ws.root)
        return 130
    except (LLMError, SearchError) as e:
        print(f"\nエラー: {e}", file=sys.stderr)
        print(f"途中まで保存済みです。修正後 `report-agent resume {ws.root}` で再開できます。",
              file=sys.stderr)
        return 1

    print("\n完成しました:")
    for o in outputs:
        print(f"  - {o}")
    return 0


def cmd_resume(args) -> int:
    ws = Workspace.open(args.dir)
    data = ws.read_yaml(ws.input_path)
    keyword = data["keyword"]
    fmt = data.get("format", "html")

    overrides = _build_overrides(args)
    # 保存済み config をベースに、今回の CLI 指定で上書きする
    saved = data.get("config", {})
    cfg = load_config(None, overrides=_merge_saved(saved, overrides))

    print(f"再開: {ws.root} (keyword={keyword})")
    print(f"完了済みステップ: {ws.load_state().get('completed', [])}")
    pipe = Pipeline(ws, cfg, keyword)
    try:
        outputs = pipe.run(fmt=fmt)
    except (LLMError, SearchError) as e:
        print(f"\nエラー: {e}", file=sys.stderr)
        print(f"再度 `report-agent resume {ws.root}` で続きから再開できます。", file=sys.stderr)
        return 1
    print("\n完成しました:")
    for o in outputs:
        print(f"  - {o}")
    return 0


def _merge_saved(saved: dict, overrides: dict) -> dict:
    import copy
    out = copy.deepcopy(saved)
    for sec, vals in (overrides or {}).items():
        out.setdefault(sec, {})
        for k, v in vals.items():
            if v is not None:
                out[sec][k] = v
    return out


def cmd_providers(_args) -> int:
    print("利用可能なプロバイダプリセット (--provider):")
    for name, preset in PROVIDER_PRESETS.items():
        url = preset["base_url"] or "(--base-url で指定)"
        print(f"  - {name:10s} -> {url}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="report-agent",
        description="キーワードからローカルLLMでレポート(HTML)を生成するエージェント",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="詳細ログ")
    sub = p.add_subparsers(dest="command", required=True)

    # 共通の LLM/検索オプションを付ける関数
    def add_common(sp):
        g = sp.add_argument_group("LLM 接続")
        g.add_argument("--provider", choices=list(PROVIDER_PRESETS.keys()),
                       help="接続先プリセット (llama.cpp / lmstudio / custom)")
        g.add_argument("--base-url", help="OpenAI互換APIのbase_url (例 http://127.0.0.1:8080/v1)")
        g.add_argument("--model", help="モデル名")
        g.add_argument("--temperature", type=float, help="生成温度")
        g.add_argument("--config", help="設定ファイル(YAML)のパス")

    # run
    rp = sub.add_parser("run", help="新規にレポートを生成する")
    rp.add_argument("keyword", help="レポートのキーワード")
    rp.add_argument("--type", choices=["research", "news", "comparison", "proposal", "tech"],
                    help="レポート種別")
    rp.add_argument("--audience", choices=["business", "consumer", "engineer", "beginner"],
                    help="読者層(consumer=一般消費者向け製品ニュース)")
    rp.add_argument("--length", type=int, help="目標総文字数")
    rp.add_argument("--title", help="レポートタイトル(省略時は自動)")
    rp.add_argument("--format", choices=["html", "md", "pdf", "all"], default="html",
                    help="出力形式(pdf はスマホ向け・画像埋め込み)")
    rp.add_argument("--searxng-url", help="SearXNG のURL")
    rp.add_argument("--time-range", choices=["day", "week", "month", "year", "off"],
                    help="鮮度フィルタ(既定: news=直近1ヶ月 / それ以外=なし)。off で無効化")
    rp.add_argument("--no-search", action="store_true", help="検索せずLLM知識のみで生成")
    rp.add_argument("--out", default="reports", help="出力先ベースディレクトリ")
    rp.add_argument("--force", action="store_true", help="接続確認に失敗しても続行")
    add_common(rp)
    rp.set_defaults(func=cmd_run)

    # resume
    sp = sub.add_parser("resume", help="途中失敗したジョブを再開する")
    sp.add_argument("dir", help="作業ディレクトリ (reports/...)")
    sp.add_argument("--searxng-url", help="SearXNG のURL(上書き)")
    add_common(sp)
    sp.set_defaults(func=cmd_resume)

    # providers
    pp = sub.add_parser("providers", help="プロバイダプリセット一覧")
    pp.set_defaults(func=cmd_providers)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "verbose", False))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
