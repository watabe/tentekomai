"""LLM 呼び出しログ (prompt / response / token / 時間) を JSONL で保存。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class CallLogger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict):
        record = {"ts": datetime.now().isoformat(), **record}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
