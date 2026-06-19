"""中間成果物の保存と再開(resume)管理。

要件定義 4-B / 6 に対応。
ディレクトリ構成:
  reports/<YYYYMMDD_slug>/
    input.yaml          実行パラメータ
    state.json          完了ステップと進捗
    research/sources.json, images.json
    outline.json
    chapters/NN_title.md
    review/issues.json
    output/report.md, report.html
    logs/llm_calls.jsonl
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

STEPS = ["plan", "research", "outline", "write", "review", "digest", "export"]


def slugify(text: str, max_len: int = 40) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w぀-ヿ一-鿿]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len] or "report"


class Workspace:
    """1レポート分の作業ディレクトリ。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    # ---- 生成/オープン ----

    @classmethod
    def create(cls, base_dir: str, keyword: str) -> "Workspace":
        date = datetime.now().strftime("%Y%m%d")
        slug = slugify(keyword)
        root = Path(base_dir) / f"{date}_{slug}"
        n = 1
        candidate = root
        while candidate.exists():
            n += 1
            candidate = Path(base_dir) / f"{date}_{slug}_{n}"
        candidate.mkdir(parents=True)
        ws = cls(candidate)
        ws._ensure_subdirs()
        return ws

    @classmethod
    def open(cls, path: str) -> "Workspace":
        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(f"作業ディレクトリが存在しません: {path}")
        ws = cls(root)
        ws._ensure_subdirs()
        return ws

    def _ensure_subdirs(self):
        for sub in ("research", "chapters", "review", "output", "logs"):
            (self.root / sub).mkdir(exist_ok=True)

    # ---- パス ----

    @property
    def input_path(self) -> Path:
        return self.root / "input.yaml"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def sources_path(self) -> Path:
        return self.root / "research" / "sources.json"

    @property
    def images_path(self) -> Path:
        return self.root / "research" / "images.json"

    @property
    def plan_path(self) -> Path:
        return self.root / "research" / "plan.json"

    @property
    def outline_path(self) -> Path:
        return self.root / "outline.json"

    @property
    def issues_path(self) -> Path:
        return self.root / "review" / "issues.json"

    @property
    def chapters_dir(self) -> Path:
        return self.root / "chapters"

    @property
    def llm_log_path(self) -> Path:
        return self.root / "logs" / "llm_calls.jsonl"

    # ---- 汎用 JSON/YAML ----

    def write_json(self, path: Path, obj: Any):
        path.write_text(
            json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def write_yaml(self, path: Path, obj: Any):
        path.write_text(
            yaml.safe_dump(obj, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def read_yaml(self, path: Path) -> Any:
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    # ---- state ----

    def load_state(self) -> dict:
        if self.state_path.exists():
            return self.read_json(self.state_path)
        return {"completed": [], "keyword": "", "created_at": datetime.now().isoformat()}

    def save_state(self, state: dict):
        self.write_json(self.state_path, state)

    def mark_done(self, step: str):
        state = self.load_state()
        if step not in state["completed"]:
            state["completed"].append(step)
        state["updated_at"] = datetime.now().isoformat()
        self.save_state(state)

    def is_done(self, step: str) -> bool:
        return step in self.load_state().get("completed", [])
