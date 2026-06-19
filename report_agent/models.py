"""パイプラインで受け渡しするデータ構造の定義。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat()


@dataclass
class Source:
    """情報源1件。要件定義 3-B のスキーマに対応。"""

    title: str
    url: str
    summary: str = ""
    published_at: str = ""
    retrieved_at: str = field(default_factory=_now_iso)
    reliability_score: float = 0.5
    engine: str = ""
    category: str = ""  # このニュースを見つけた検索クエリ(=ダイジェストのカテゴリ)
    image_url: str = ""  # 記事のサムネ(og:image 等)。無ければ空

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Source":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ImageRef:
    """挿絵用の画像参照。HTML 出力で <img> として埋め込む。"""

    title: str
    img_src: str
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImageRef":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class ChapterPlan:
    """アウトライン上の1章の計画。"""

    index: int
    title: str
    purpose: str = ""
    points: list[str] = field(default_factory=list)
    source_indices: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChapterPlan":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class NewsItem:
    """ニュースダイジェストの1項目。"""

    source_index: int
    headline: str
    summary: str
    url: str = ""
    published_at: str = ""
    reliability_score: float = 0.5
    image_url: str = ""  # 記事サムネ画像URL(無ければ空。HTMLでfaviconにフォールバック)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NewsItem":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class NewsCategory:
    """ニュースのカテゴリ(=章相当)と、その配下の項目。"""

    title: str
    items: list[NewsItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "items": [i.to_dict() for i in self.items]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NewsCategory":
        return cls(
            title=d.get("title", ""),
            items=[NewsItem.from_dict(i) for i in d.get("items", [])],
        )


@dataclass
class ChapterResult:
    """生成された章の本文と検証結果。"""

    index: int
    title: str
    body: str
    char_count: int = 0
    issues: list[str] = field(default_factory=list)
    truncated: bool = False
    attempts: int = 1

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChapterResult":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})
