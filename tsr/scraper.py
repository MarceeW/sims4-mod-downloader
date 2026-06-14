"""Listing scraper: walks TSR browse pages and yields :class:`Item` objects.

Each browse page is server-rendered and embeds every item as a
``div.item-wrapper`` whose ``data-item`` attribute is a complete JSON record, so
one HTTP request per page is enough -- no per-item detail fetch required.
"""

from __future__ import annotations

import json
import math
import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from . import ALLOWED_HOST, BASE, ITEMS_PER_PAGE
from .models import Item

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_CATEGORY_RE = re.compile(r"/downloads/browse/category/([^/]+)")
_PAGE_RE = re.compile(r"/page/(\d+)")
_TOTAL_RE = re.compile(r"Page\s*</?\w*>?\s*\d+\s*/\s*(\d+)")
_CNT_RE = re.compile(r"/page/\d+/cnt/(\d+)/")

Logger = Callable[[str], None]


class ScrapeError(ValueError):
    """Raised for an invalid or off-domain entry URL."""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def parse_entry_url(url: str) -> tuple[str, int]:
    """Validate a browse URL and extract ``(category, start_page)``.

    The host is locked to ``www.thesimsresource.com``. The category is taken from
    ``/downloads/browse/category/{category}/`` and the optional starting page from
    ``/page/{N}/`` (defaults to 1).
    """
    url = (url or "").strip()
    if not url:
        raise ScrapeError("A belépő URL üres.")
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host not in (ALLOWED_HOST, "thesimsresource.com"):
        raise ScrapeError(
            f"Idegen domain: '{host or url}'. Csak a thesimsresource.com engedélyezett."
        )

    cat_match = _CATEGORY_RE.search(parsed.path)
    if not cat_match:
        raise ScrapeError(
            "Az URL nem böngésző-lista "
            "(elvárt formátum: /downloads/browse/category/<kategória>/)."
        )
    category = cat_match.group(1)

    page_match = _PAGE_RE.search(parsed.path)
    start_page = int(page_match.group(1)) if page_match else 1
    return category, max(1, start_page)


def parse_entry_urls(text: str) -> list[tuple[str, int]]:
    """Parse one or more entry URLs (separated by newlines or commas) into a
    de-duplicated list of ``(category, start_page)`` pairs, preserving order.

    Raises :class:`ScrapeError` (naming the offending line) if any URL is
    invalid, or if no URL is given.
    """
    entries: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for raw in re.split(r"[\n,]+", text or ""):
        raw = raw.strip()
        if not raw:
            continue
        try:
            pair = parse_entry_url(raw)
        except ScrapeError as exc:
            raise ScrapeError(f"'{raw}': {exc}") from exc
        if pair not in seen:
            seen.add(pair)
            entries.append(pair)
    if not entries:
        raise ScrapeError("Adj meg legalább egy belépő URL-t.")
    return entries


def page_url(category: str, page: int) -> str:
    """Build a clean listing URL (no stale ``cnt/`` segment)."""
    return f"{BASE}/downloads/browse/category/{category}/page/{page}/"


def _fetch(session: requests.Session, url: str) -> str:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _parse_items(html: str) -> list[Item]:
    soup = BeautifulSoup(html, "lxml")
    items: list[Item] = []
    for wrap in soup.select("div.item-wrapper[data-item]"):
        raw = wrap.get("data-item")
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        item = Item.from_data_item(data)
        if item.id:
            items.append(item)
    return items


def get_total_pages(category: str, session: requests.Session | None = None) -> int:
    """Total number of listing pages for a category (best-effort, min 1)."""
    session = session or _session()
    html = _fetch(session, page_url(category, 1))

    m = _TOTAL_RE.search(html)
    if m:
        return max(1, int(m.group(1)))

    cnt_match = _CNT_RE.search(html)
    if cnt_match:
        return max(1, math.ceil(int(cnt_match.group(1)) / ITEMS_PER_PAGE))

    # Fallback: at least the pages we can see linked, else 1.
    pages = [int(p) for p in re.findall(r"/page/(\d+)/", html)]
    return max(pages) if pages else 1


def iter_items(
    category: str,
    start: int,
    end: int,
    stop_event: threading.Event | None = None,
    on_log: Logger | None = None,
    delay_range: tuple[float, float] = (2.0, 4.0),
    session: requests.Session | None = None,
) -> Iterator[Item]:
    """Yield items from page ``start`` through ``end`` (inclusive).

    ``stop_event`` lets a caller abort between pages. A randomized delay is
    applied between page fetches to stay polite.
    """
    session = session or _session()
    log = on_log or (lambda _msg: None)

    for page in range(start, end + 1):
        if stop_event and stop_event.is_set():
            log("Leállítva a(z) %d. oldal előtt." % page)
            return

        url = page_url(category, page)
        log(f"Oldal letöltése {page}/{end}: {url}")
        try:
            html = _fetch(session, url)
        except requests.RequestException as exc:
            log(f"  ! a(z) {page}. oldal hibás: {exc}")
            continue

        items = _parse_items(html)
        log(f"  {len(items)} elem a(z) {page}. oldalon")
        for item in items:
            if stop_event and stop_event.is_set():
                return
            yield item

        if page < end:
            time.sleep(random.uniform(*delay_range))
