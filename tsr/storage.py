"""Persistence helpers: a resume manifest plus JSON/CSV metadata export."""

from __future__ import annotations

import csv
import glob
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from .models import Item

MANIFEST_NAME = "manifest.json"


def existing_download(folder, item_id: str) -> Path | None:
    """Return an already-downloaded file for ``item_id`` if one is present in
    ``folder`` (files are named ``<id>-<name>``), else ``None``. Lets a re-run
    skip items whose file exists even without a manifest entry."""
    folder = Path(folder)
    if not folder.is_dir() or not item_id:
        return None
    for p in folder.glob(f"{glob.escape(item_id)}-*"):
        if p.is_file():
            return p
    return None

_CSV_COLUMNS = [
    "id",
    "title",
    "creator",
    "category",
    "category_display",
    "downloads",
    "filesize",
    "published",
    "keywords",
    "detail_url",
]


class Manifest:
    """Tracks per-item download state in ``<folder>/manifest.json`` so re-runs
    can skip work already done."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / MANIFEST_NAME
        self._data: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def is_done(self, item: Item) -> bool:
        entry = self._data.get(item.id)
        if not entry or entry.get("status") != "done":
            return False
        file_name = entry.get("file")
        # Treat as done only if the file is actually present.
        return bool(file_name) and (self.folder / file_name).exists()

    def mark(self, item: Item, status: str, file_name: str | None = None) -> None:
        self._data[item.id] = {
            "title": item.title,
            "status": status,
            "file": file_name or "",
            "ts": datetime.now(tz=timezone.utc).isoformat(),
        }
        self.save()

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def export_metadata(
    items: Iterable[Item],
    folder: Path,
    as_json: bool = True,
    as_csv: bool = True,
) -> list[Path]:
    """Write ``metadata.json`` and/or ``metadata.csv`` into ``folder``."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    records = [it.to_record() for it in items]
    written: list[Path] = []

    if as_json:
        p = folder / "metadata.json"
        p.write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        written.append(p)

    if as_csv:
        p = folder / "metadata.csv"
        with p.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
            writer.writeheader()
            for rec in records:
                writer.writerow({k: rec.get(k, "") for k in _CSV_COLUMNS})
        written.append(p)

    return written
