"""Browser-automation downloader (Playwright sync API).

A non-VIP user cannot fetch free files over plain HTTP. The confirmed free-user
flow is:

1. On the item's detail page, click ``a.download-button.dl.nonsubscriber``.
2. The page navigates to a ticketed URL
   ``/downloads/download/itemId/{id}/ticket/{ticket}`` (an ad interstitial with
   a short countdown -- ``waitTime`` is ~4s).
3. The page JS only advances the countdown while the tab is *visible* / ads have
   loaded, then un-disables the real trigger ``a.downloader``.
4. Clicking ``a.downloader`` makes the browser download the actual ``.package`` /
   ``.zip`` file.

We **wait out the countdown** (we do not zero it); we only keep the page
reporting "visible" so a headless tab behaves like a normal foreground tab.

Must run on its own thread -- the Playwright sync API cannot share the Tk main
loop's thread.
"""

from __future__ import annotations

import os
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from . import BASE
from .models import Item

Logger = Callable[[str], None]


def _configure_bundled_browsers() -> None:
    """When running as a PyInstaller bundle, point Playwright at a Chromium
    shipped next to the executable if one is present (a ``ms-playwright`` folder).
    Harmless in a normal Python run."""
    if not getattr(sys, "frozen", False):
        return
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        return
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for candidate in (base / "ms-playwright", Path(sys.executable).parent / "ms-playwright"):
        if candidate.is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(candidate)
            return

_TICKET_URL_RE = re.compile(r"/downloads/download/itemId/")
_TICKET_RE = re.compile(r"/downloads/download/itemId/(\d+)/ticket/([^/?#]+)")

# Keep the page reporting visible so the interstitial's visibility-gated
# countdown advances (it defaults to a ~4s wait). This does not skip the timer.
_VISIBILITY_SCRIPT = """
Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});
Object.defineProperty(document, 'hidden', {get: () => false});
document.hasFocus = () => true;
"""

CONSENT_SELECTORS = [
    ".fc-cta-consent",
    "#onetrust-accept-btn-handler",
    "button:has-text('Accept')",
    "button:has-text('AGREE')",
]
# The button on the detail page that starts the free flow.
START_SELECTORS = [
    "a.download-button.dl.nonsubscriber",
    "a.download-button.dl",
    "a.okletsdothis.dl",
]
# The real trigger on the ticketed interstitial, once the countdown completes.
READY_SELECTOR = "a.downloader:not(.disabled)"

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class DownloaderUnavailable(RuntimeError):
    """Raised when Playwright (or its browser) is not installed."""


def _dest_name(item: Item, suggested: str) -> str:
    """``<id>-<server filename>`` (sanitized), keeping the server's extension."""
    suggested = _SAFE.sub("_", suggested).strip("_") or "download"
    return f"{item.id}-{suggested}"


class TSRDownloader:
    """Manages a single browser context reused across all items."""

    def __init__(
        self,
        headless: bool = True,
        on_log: Logger | None = None,
        nav_timeout: int = 60_000,
        ready_timeout: int = 45_000,
        download_timeout: int = 60_000,
    ):
        self.headless = headless
        self.log = on_log or (lambda _msg: None)
        self.nav_timeout = nav_timeout
        self.ready_timeout = ready_timeout
        self.download_timeout = download_timeout
        self._pw = None
        self._browser = None
        self._context = None
        self._consent_done = False

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "TSRDownloader":
        _configure_bundled_browsers()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - env dependent
            raise DownloaderUnavailable(
                "A Playwright nincs telepítve. Futtasd: pip install playwright "
                "&& playwright install chromium"
            ) from exc

        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
        except Exception as exc:  # pragma: no cover - env dependent
            raise DownloaderUnavailable(
                f"Nem indítható a Chromium ({exc}). Futtasd: playwright install chromium"
            ) from exc

        self._context = self._browser.new_context(accept_downloads=True)
        self._context.add_init_script(_VISIBILITY_SCRIPT)
        self._context.set_default_timeout(self.nav_timeout)
        return self

    def __exit__(self, *exc) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    def _close_stray_pages(self, keep) -> None:
        """Close ad pop-up tabs opened during the flow. Called from the main
        flow (never from inside an event handler, which would stall the sync
        API mid-click)."""
        if not self._context:
            return
        for p in list(self._context.pages):
            if p is not keep:
                try:
                    p.close()
                except Exception:
                    pass

    # -- helpers -----------------------------------------------------------
    def _dismiss_consent(self, page) -> None:
        if self._consent_done:
            return
        for sel in CONSENT_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.count() and btn.is_visible():
                    btn.click(timeout=4_000)
                    self.log("  süti-figyelmeztetés elfogadva")
                    break
            except Exception:
                continue
        self._consent_done = True

    def _click_start(self, page) -> bool:
        """Click the detail-page download button and confirm it navigated to the
        ticketed interstitial. Tries a normal click, a forced click, then a JS
        dispatch (the button runs an onclick handler), verifying the ticket
        navigation after each attempt so a no-op click is not reported as
        success."""
        for sel in START_SELECTORS:
            loc = page.locator(sel).first
            try:
                loc.wait_for(state="visible", timeout=12_000)
            except Exception:
                continue
            try:
                loc.scroll_into_view_if_needed(timeout=4_000)
            except Exception:
                pass
            for method in ("click", "force", "dispatch"):
                try:
                    if method == "dispatch":
                        loc.dispatch_event("click")
                    else:
                        loc.click(timeout=8_000, force=(method == "force"))
                except Exception:
                    continue
                try:
                    page.wait_for_url(_TICKET_URL_RE, timeout=8_000)
                    return True
                except Exception:
                    continue
        return False

    def _blocked_reason(self, page) -> str:
        """Best-effort explanation when the start click did not reach a ticket
        (e.g. a login/VIP gate, common for mods with required items)."""
        try:
            body = page.locator("body").inner_text().lower()
        except Exception:
            return "nem indult el a letöltés (nincs ticket)"
        if "vip subscription is required" in body or "need to login to download" in body:
            return "bejelentkezés / VIP szükséges (anonim módban nem ingyenes)"
        if "this creation has requirements" in body:
            return "függőségei vannak / korlátozott letöltés"
        return "nem indult el a letöltés (nincs ticket)"

    # -- main entry point --------------------------------------------------
    def download(
        self,
        item: Item,
        dest_dir: Path,
        stop_event: threading.Event | None = None,
    ) -> Path | None:
        """Download one item's file into ``dest_dir``; return the saved path, or
        ``None`` on any failure (never raises for a single item)."""
        if stop_event and stop_event.is_set():
            return None
        if not item.detail_url:
            self.log(f"  ! nincs részletező URL ehhez: {item.id}")
            return None

        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        page = self._context.new_page()
        try:
            page.goto(item.detail_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1_200)
            self._dismiss_consent(page)
            page.wait_for_timeout(400)

            if not self._click_start(page):
                self.log(f"  ! kihagyva {item.id}: {self._blocked_reason(page)}")
                return None

            # _click_start confirmed we are on the ticketed interstitial URL.
            # Grab the ticket so we can trigger the file via the same URL the
            # page's own handler uses (avoids ad-overlay-blocked link clicks).
            m = _TICKET_RE.search(page.url)
            if not m:
                self.log(f"  ! nincs letöltési ticket ehhez: {item.id}")
                return None
            ticket = m.group(2)

            # Wait out the interstitial countdown (~4s) until the trigger enables
            # -- we honor the timer rather than forcing it.
            page.wait_for_selector(READY_SELECTOR, timeout=self.ready_timeout)

            # The enabled trigger navigates to /downloads/thankyou/.../ticket/...,
            # which serves the file. Drive that navigation directly.
            thankyou = f"{BASE}/downloads/thankyou/id/{item.id}/ticket/{ticket}"
            with page.expect_download(timeout=self.download_timeout) as dl_info:
                page.evaluate("u => { document.location.href = u; }", thankyou)
            download = dl_info.value

            target = dest_dir / _dest_name(item, download.suggested_filename)
            download.save_as(str(target))
            self.log(f"  mentve: {target.name} ({target.stat().st_size} bájt)")
            return target
        except Exception as exc:
            msg = str(exc).splitlines()[0][:120]
            self.log(f"  ! sikertelen letöltés ({item.id}): {msg}")
            return None
        finally:
            self._close_stray_pages(keep=page)
            try:
                page.close()
            except Exception:
                pass
