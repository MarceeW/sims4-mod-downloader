"""Listing scraper: walks TSR browse pages and yields :class:`Item` objects.

Each browse page is server-rendered and embeds every item as a
``div.item-wrapper`` whose ``data-item`` attribute is a complete JSON record, so
one HTTP request per page is enough -- no per-item detail fetch required.

Entry URLs are templates: a ``#`` placeholder marks where the page number goes
(e.g. ``.../page/#`` or ``.../skipsetitems/1/page/#``). The scraper substitutes
1, 2, 3 ... and steps forward until a page repeats the previous page's items
(TSR clamps overflow pages to the last page rather than returning an empty one)
or yields nothing. URLs without ``#`` are fetched once.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from . import ALLOWED_HOST, BASE
from .models import Item

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

PAGE_PLACEHOLDER = "#"

Logger = Callable[[str], None]


class ScrapeError(ValueError):
    """Raised for an invalid or off-domain entry URL."""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def validate_entry_url(url: str) -> str:
    """Normalize and validate a single entry URL (host locked to
    thesimsresource.com). Returns the URL unchanged otherwise -- any path scheme
    is accepted (``/downloads/browse/...``, ``/members/...``, ``/artists/...``),
    optionally containing a ``#`` page placeholder."""
    url = (url or "").strip()
    if not url:
        raise ScrapeError("A belépő URL üres.")
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).netloc or "").lower()
    if host not in (ALLOWED_HOST, "thesimsresource.com"):
        raise ScrapeError(
            f"Idegen domain: '{host or url}'. Csak a thesimsresource.com engedélyezett."
        )
    return url


def parse_entry_urls(text: str) -> list[str]:
    """Parse one or more entry URLs (separated by newlines or commas) into a
    de-duplicated list of URL templates, preserving order.

    Raises :class:`ScrapeError` (naming the offending line) if any URL is
    invalid, or if no URL is given.
    """
    entries: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[\n,]+", text or ""):
        raw = raw.strip()
        if not raw:
            continue
        try:
            url = validate_entry_url(raw)
        except ScrapeError as exc:
            raise ScrapeError(f"'{raw}': {exc}") from exc
        if url not in seen:
            seen.add(url)
            entries.append(url)
    if not entries:
        raise ScrapeError("Adj meg legalább egy belépő URL-t.")
    return entries


def creator_templates(name: str) -> list[str]:
    """Build candidate listing-URL templates (with a ``#`` page placeholder) for
    a creator name, covering both the ``/members/`` and ``/artists/`` paths.
    ``skipsetitems/1`` avoids listing individual items of sets twice."""
    name = (name or "").strip().lstrip("@").strip("/")
    return [
        f"{BASE}/members/{name}/downloads/browse/category/sims4/skipsetitems/1/page/#",
        f"{BASE}/artists/{name}/downloads/browse/category/sims4/skipsetitems/1/page/#",
    ]


def _page_belongs_to(html: str, name: str) -> bool:
    """True if page 1 mostly lists items by ``name``. An unknown creator name
    silently falls back to the general Sims 4 listing (mixed creators), so we
    confirm the items' ``minisiteName``/``creatorName`` match the requested name."""
    target = (name or "").strip().lstrip("@").strip("/").lower()
    soup = BeautifulSoup(html, "lxml")
    names: list[str] = []
    for wrap in soup.select("div.item-wrapper[data-item]"):
        try:
            d = json.loads(wrap["data-item"])
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
        names.append(str(d.get("minisiteName") or d.get("creatorName") or "").lower())
    if not names:
        return False
    matches = sum(1 for n in names if n == target)
    return matches >= len(names) * 0.6


def resolve_creator(
    name: str,
    session: requests.Session | None = None,
    on_log: Logger | None = None,
) -> str | None:
    """Return the working listing-URL template for a creator (tries members then
    artists), or ``None`` if the name doesn't match a real creator page."""
    session = session or _session()
    for tmpl in creator_templates(name):
        probe = tmpl.replace(PAGE_PLACEHOLDER, "1")
        try:
            html = _fetch(session, probe)
        except requests.RequestException:
            continue
        if _page_belongs_to(html, name):
            return tmpl
    return None


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


def iter_items(
    url_template: str,
    stop_event: threading.Event | None = None,
    on_log: Logger | None = None,
    delay_range: tuple[float, float] = (2.0, 4.0),
    max_pages: int | None = None,
    session: requests.Session | None = None,
) -> Iterator[Item]:
    """Yield items for one entry URL, stepping pages via the ``#`` placeholder.

    Stops when a page repeats the previous page's item IDs (TSR clamps overflow
    to the last page), when a page yields no items, after ``max_pages`` pages
    (if given), or when ``stop_event`` is set. URLs without ``#`` are fetched
    once. A randomized delay is applied between page fetches to stay polite.
    """
    session = session or _session()
    log = on_log or (lambda _msg: None)
    paginated = PAGE_PLACEHOLDER in url_template

    page = 1
    prev_ids: list[str] | None = None
    while True:
        if stop_event and stop_event.is_set():
            return
        if max_pages and page > max_pages:
            log(f"  elérve a max oldal/URL ({max_pages})")
            return

        url = url_template.replace(PAGE_PLACEHOLDER, str(page)) if paginated else url_template
        log(f"Oldal letöltése #{page}: {url}")
        try:
            html = _fetch(session, url)
        except requests.RequestException as exc:
            log(f"  ! a(z) {page}. oldal hibás: {exc}")
            return

        items = _parse_items(html)
        ids = [it.id for it in items]
        if not items:
            log(f"  nincs több elem (oldal {page}) — vége")
            return
        if ids == prev_ids:
            log(f"  ismétlődő oldal (oldal {page}) — az URL végére értünk")
            return
        prev_ids = ids

        log(f"  {len(items)} elem (oldal {page})")
        for item in items:
            if stop_event and stop_event.is_set():
                return
            yield item

        if not paginated:
            return
        page += 1
        time.sleep(random.uniform(*delay_range))
