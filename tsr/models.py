"""Data model for a single TSR download item, parsed from the ``data-item`` JSON
blob embedded in each ``div.item-wrapper`` on a browse listing page."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from . import BASE


def _slug(text: str) -> str:
    """Turn a title into a URL/filename-safe slug."""
    text = re.sub(r"&amp;", "&", text or "")
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "item"


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Item:
    id: str
    title: str
    creator: str
    category: str
    category_display: str
    downloads: int
    filesize: int
    published: datetime | None
    keywords: list[str] = field(default_factory=list)
    detail_url: str = ""

    @classmethod
    def from_data_item(cls, d: dict) -> "Item":
        item_id = str(d.get("ID") or d.get("ItemID") or "").strip()
        title = (d.get("title") or "").replace("&amp;", "&").strip()
        category = (d.get("PrimaryCategory") or "").strip()

        # publishDate is a unix timestamp string; "0" means unknown.
        published = None
        ts = _to_int(d.get("publishDate"), 0)
        if ts > 0:
            published = datetime.fromtimestamp(ts, tz=timezone.utc)

        # keywords may arrive as a comma string or a list of {Phrase} dicts.
        keywords: list[str] = []
        kw = d.get("keywords")
        if isinstance(kw, str) and kw:
            keywords = [k.strip() for k in kw.split(",") if k.strip()]
        elif isinstance(d.get("keywordsArr"), list):
            keywords = [k.get("Phrase", "") for k in d["keywordsArr"] if k.get("Phrase")]

        slug = (d.get("urlTitle") or "").strip() or _slug(title)
        detail_url = ""
        if category and item_id:
            detail_url = f"{BASE}/downloads/details/category/{category}/title/{slug}/id/{item_id}/"

        return cls(
            id=item_id,
            title=title,
            creator=(d.get("creator") or d.get("creatorName") or "").strip(),
            category=category,
            category_display=(d.get("CategoryDisplay") or "").strip(),
            downloads=_to_int(d.get("downloads")),
            filesize=_to_int(d.get("FileSize") or d.get("filesize")),
            published=published,
            keywords=keywords,
            detail_url=detail_url,
        )

    @property
    def filename(self) -> str:
        """Stable, filesystem-safe target name for the downloaded archive."""
        return f"{self.id}-{_slug(self.title)}.zip"

    def to_record(self) -> dict:
        """Flat dict for JSON/CSV export."""
        rec = asdict(self)
        rec["published"] = self.published.isoformat() if self.published else ""
        rec["keywords"] = ", ".join(self.keywords)
        return rec
