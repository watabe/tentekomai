"""設定の読み込みとプロバイダプリセット。

優先順位: CLI 引数 > 設定ファイル(config.yaml) > プリセット既定値。
LLM 接続は起動時に --provider で選択するか、--base-url 等で直接指定する。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# プロバイダプリセット: --provider で選ぶと base_url が決まる。
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "llama.cpp": {"base_url": "http://127.0.0.1:8080/v1"},
    "lmstudio": {"base_url": "http://localhost:1234/v1"},
    # custom は base_url を CLI/設定ファイルで明示する前提。
    "custom": {"base_url": ""},
}

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "llama.cpp",
        "base_url": "",  # 空なら provider プリセットを使う
        "api_key": "not-needed",
        "model": "local-model",
        "temperature": 0.3,
        "timeout_sec": 600,   # 思考型モデルは1回の生成が長いので長めに取る
        "max_retries": 3,
    },
    "generation": {
        # 注: reasoning(思考)型モデル(GLM/Qwen3 等)は回答前に思考トークンを
        # 消費するため、max_tokens は本文+思考分を見込んで大きめに取る。
        "outline_max_tokens": 2500,
        "chapter_max_tokens": 3000,
        "review_max_tokens": 1500,
        # 章ごとの安全な生成単位(文字数)。要件定義 3-C より 800〜1,500字。
        "chapter_target_chars": 1000,
        # news ダイジェスト用
        "digest_cat_max_tokens": 3000,   # カテゴリ分類(多件数だと出力が長くなる)
        "digest_sum_max_tokens": 3000,   # 要約バッチ
        "summary_batch_size": 6,         # 1回の要約で扱うニュース件数(小さめで安定)
        "max_news_categories": 6,
        "max_news_items": 45,            # ダイジェストに載せる最大件数(多すぎ防止)
    },
    "search": {
        "enabled": True,
        "searxng_url": "http://localhost:8888",
        "max_results": 8,
        "max_images": 6,
        "language": "ja",
        "timeout_sec": 30,
        # news ダイジェスト時はクエリ数・件数を増やして多数のニュースを集める
        "news_queries": 9,
        "news_max_results": 12,
        # 鮮度フィルタ(SearXNG time_range): "" / day / week / month / year
        # None=未指定(news は news_time_range、それ以外はオフ)。CLI --time-range で上書き可。
        "time_range": None,
        "news_time_range": "month",
    },
    "report": {
        "type": "research",       # research / news / comparison / proposal / tech
        "audience": "business",   # business / consumer / engineer / beginner
        "length": 5000,           # 目標総文字数
        "title": "",              # 空ならキーワードから自動生成
    },
}


@dataclass
class Config:
    llm: dict[str, Any] = field(default_factory=dict)
    generation: dict[str, Any] = field(default_factory=dict)
    search: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        """実効 base_url。明示指定 > プリセット。"""
        explicit = (self.llm.get("base_url") or "").strip()
        if explicit:
            return explicit.rstrip("/")
        preset = PROVIDER_PRESETS.get(self.llm.get("provider", ""), {})
        return (preset.get("base_url", "") or "").rstrip("/")

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm": self.llm,
            "generation": self.generation,
            "search": self.search,
            "report": self.report,
        }


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load_config(config_path: str | None = None, overrides: dict | None = None) -> Config:
    """設定ファイルと CLI オーバーライドをマージして Config を返す。"""
    merged = copy.deepcopy(DEFAULT_CONFIG)

    if config_path:
        p = Path(config_path)
        if not p.exists():
            raise FileNotFoundError(f"設定ファイルが見つかりません: {config_path}")
        with p.open("r", encoding="utf-8") as f:
            file_cfg = yaml.safe_load(f) or {}
        merged = _deep_merge(merged, file_cfg)

    if overrides:
        merged = _deep_merge(merged, overrides)

    return Config(
        llm=merged["llm"],
        generation=merged["generation"],
        search=merged["search"],
        report=merged["report"],
    )
