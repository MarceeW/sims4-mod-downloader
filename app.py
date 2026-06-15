#!/usr/bin/env python3
"""Sims-styled Tkinter GUI for the TSR Sims 4 free-mods downloader.

Run:  python app.py

You can download by **creator name** (the app finds their page automatically)
and/or by pasting **listing URLs**. The scraping + (Playwright) download work
runs on a background thread; the GUI polls a thread-safe queue for log/progress
updates so the window stays responsive and Tk widgets are only touched from the
main thread.
"""

from __future__ import annotations

import queue
import random
import re
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tsr import config, scraper, storage
from tsr.scraper import ScrapeError

TOS_NOTICE = (
    "Ez az eszköz a thesimsresource.com normál, ingyenes letöltési folyamatát "
    "automatizálja (kivárja a hirdetés-visszaszámlálót, és nem kerül meg semmilyen "
    "korlátozást).\n\n"
    "Megjegyzés: az oldal robots.txt-je tiltja a letöltési végpontot, és a tömeges "
    "automatizált letöltés ütközhet a TSR felhasználási feltételeivel. A használat "
    "a te felelősséged. Kérlek, tartsd a késleltetést kíméletesen, és csak olyan "
    "tartalmat tölts le, amire jogosult vagy.\n\nFolytatod?"
)

# -- Sims-flavored palette -------------------------------------------------
BG = "#E7F5EC"          # soft mint window background
CARD = "#FFFFFF"        # white panels
BORDER = "#BFE3CB"      # subtle green border
GREEN = "#3DBA4E"       # plumbob green (accents/buttons)
GREEN_DK = "#2C8E3A"    # darker green (hover/headings)
GREEN_LT = "#D5F0DC"    # light green (progress trough)
INK = "#1F3A2A"         # dark text
MUTED = "#6E8A78"       # hint text
RED = "#E0573E"         # stop button
RED_DK = "#BE4530"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("🌿 Sims 4 Mod Letöltő")
        root.geometry("900x860")
        root.minsize(780, 680)
        root.configure(bg=BG)

        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._tos_acknowledged = False
        self._config = config.load_config()

        self._init_fonts_and_style()
        self._build_ui()
        self._apply_config()
        self.root.after(100, self._poll_queue)

    # -- styling helpers ---------------------------------------------------
    def _init_fonts_and_style(self) -> None:
        fams = set(tkfont.families())
        head = next((f for f in ("Chalkboard SE", "Comic Sans MS", "Futura",
                                  "Verdana", "Helvetica") if f in fams), "Helvetica")
        body = next((f for f in ("Helvetica Neue", "Helvetica", "Arial") if f in fams),
                    "Helvetica")
        self.f_title = tkfont.Font(family=head, size=24, weight="bold")
        self.f_sub = tkfont.Font(family=body, size=11)
        self.f_h2 = tkfont.Font(family=head, size=14, weight="bold")
        self.f_body = tkfont.Font(family=body, size=12)
        self.f_hint = tkfont.Font(family=body, size=10, slant="italic")
        self.f_btn = tkfont.Font(family=head, size=15, weight="bold")
        self.f_mono = tkfont.Font(family="Menlo" if "Menlo" in fams else body, size=10)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Sims.Horizontal.TProgressbar",
            troughcolor=GREEN_LT, background=GREEN,
            bordercolor=GREEN_LT, lightcolor=GREEN, darkcolor=GREEN_DK,
            thickness=20,
        )

    def _card(self, parent, title: str, expand: bool = False):
        """A titled white panel; returns the inner frame to fill with widgets."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both" if expand else "x", expand=expand, pady=(0, 12))
        tk.Label(wrap, text=title, bg=BG, fg=GREEN_DK, font=self.f_h2,
                 anchor="w").pack(fill="x", pady=(0, 4))
        card = tk.Frame(wrap, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="both", expand=expand)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=expand, padx=14, pady=12)
        return inner

    def _hint(self, parent, text: str) -> None:
        tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=self.f_hint,
                 anchor="w", justify="left").pack(fill="x", pady=(2, 0))

    def _textbox(self, parent, height: int) -> tk.Text:
        t = tk.Text(parent, height=height, wrap="none", font=self.f_body,
                    bg="#FBFFFC", fg=INK, relief="flat", highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=GREEN,
                    padx=8, pady=6, insertbackground=INK)
        t.pack(fill="x")
        return t

    def _check(self, parent, text, var, cmd=None):
        return tk.Checkbutton(
            parent, text=text, variable=var, command=cmd, bg=CARD, fg=INK,
            activebackground=CARD, activeforeground=GREEN_DK, selectcolor="#FFFFFF",
            font=self.f_body, anchor="w", highlightthickness=0, bd=0,
        )

    def _spin(self, parent, frm, to, var, width, inc=1):
        return tk.Spinbox(
            parent, from_=frm, to=to, textvariable=var, width=width, increment=inc,
            font=self.f_body, relief="flat", highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=GREEN, buttonbackground=GREEN_LT,
        )

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=CARD, fg=INK, font=self.f_body)

    def _button(self, parent, text, cmd, color, color_dk):
        return tk.Button(
            parent, text=text, command=cmd, font=self.f_btn, fg="white", bg=color,
            activebackground=color_dk, activeforeground="white", relief="flat",
            bd=0, padx=22, pady=10, cursor="hand2", highlightthickness=0,
            disabledforeground="#EaEaEa",
        )

    def _plumbob(self, parent) -> tk.Canvas:
        cv = tk.Canvas(parent, width=42, height=54, bg=GREEN, highlightthickness=0)
        # Faceted green gem (top point, mid waist, bottom point).
        cv.create_polygon(21, 2, 39, 23, 21, 51, 3, 23, fill="#A7F0B0", outline="")
        cv.create_polygon(21, 2, 30, 23, 21, 31, 12, 23, fill="#6FE085", outline="")
        cv.create_polygon(21, 31, 30, 23, 39, 23, 21, 51, fill="#2E9E42", outline="")
        cv.create_polygon(21, 31, 12, 23, 3, 23, 21, 51, fill="#37B14E", outline="")
        return cv

    # -- UI construction ---------------------------------------------------
    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=GREEN)
        header.pack(fill="x")
        inner = tk.Frame(header, bg=GREEN)
        inner.pack(padx=18, pady=14, anchor="w", fill="x")
        self._plumbob(inner).pack(side="left", padx=(0, 14))
        titles = tk.Frame(inner, bg=GREEN)
        titles.pack(side="left", anchor="w")
        tk.Label(titles, text="Sims 4 Mod Letöltő", bg=GREEN, fg="white",
                 font=self.f_title).pack(anchor="w")
        tk.Label(titles, text="Töltsd le kedvenc alkotóid munkáit a The Sims Resource-ról",
                 bg=GREEN, fg="#EAFBEE", font=self.f_sub).pack(anchor="w")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)

        # --- Sources card -------------------------------------------------
        src = self._card(body, "🎯 Mit töltsünk le?")
        tk.Label(src, text="👤  Alkotók  (egy név soronként)", bg=CARD, fg=INK,
                 font=self.f_body, anchor="w").pack(fill="x")
        self.creator_text = self._textbox(src, height=3)
        self._hint(src, "Pl. Leah_Lillith — a profil URL-jében szereplő név. "
                        "Az app megkeresi és letölti az összes Sims 4 munkáját.")
        tk.Frame(src, bg=CARD, height=8).pack()
        tk.Label(src, text="🔗  Vagy konkrét linkek  (egy URL soronként, # = oldalszám)",
                 bg=CARD, fg=INK, font=self.f_body, anchor="w").pack(fill="x")
        self.url_text = self._textbox(src, height=3)
        self._hint(src, "Pl. .../downloads/browse/category/sims4-mods/page/#")

        # --- Folder card --------------------------------------------------
        dst = self._card(body, "📁 Hova mentsen?")
        rowf = tk.Frame(dst, bg=CARD)
        rowf.pack(fill="x")
        self.folder_var = tk.StringVar(value=str(Path.cwd() / "downloads"))
        tk.Entry(rowf, textvariable=self.folder_var, font=self.f_body, bg="#FBFFFC",
                 fg=INK, relief="flat", highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=GREEN).pack(side="left", fill="x", expand=True, ipady=5)
        self._button(rowf, "Tallózás…", self._pick_folder, GREEN, GREEN_DK).pack(
            side="left", padx=(10, 0))

        # --- Settings card ------------------------------------------------
        st = self._card(body, "⚙️ Beállítások")
        grid = tk.Frame(st, bg=CARD)
        grid.pack(fill="x")
        for col in (1, 3):
            grid.columnconfigure(col, weight=0)

        self.workers_var = tk.IntVar(value=3)
        self.max_items_var = tk.IntVar(value=0)
        self.end_page_var = tk.IntVar(value=1)
        self.all_pages_var = tk.BooleanVar(value=True)
        self.delay_min_var = tk.DoubleVar(value=2.0)
        self.delay_max_var = tk.DoubleVar(value=4.0)
        self.metadata_only_var = tk.BooleanVar(value=True)
        self.headless_var = tk.BooleanVar(value=True)

        self._label(grid, "Párhuzamos letöltés:").grid(row=0, column=0, sticky="w", pady=5)
        self._spin(grid, 1, 1000000, self.workers_var, 6).grid(row=0, column=1, sticky="w", padx=(6, 24))
        self._label(grid, "Max. elem (0 = összes):").grid(row=0, column=2, sticky="w", pady=5)
        self._spin(grid, 0, 10000000, self.max_items_var, 9).grid(row=0, column=3, sticky="w", padx=(6, 0))

        self._label(grid, "Késleltetés (mp):").grid(row=1, column=0, sticky="w", pady=5)
        drow = tk.Frame(grid, bg=CARD)
        drow.grid(row=1, column=1, sticky="w", padx=(6, 24))
        self._spin(drow, 0, 60, self.delay_min_var, 4, inc=0.5).pack(side="left")
        tk.Label(drow, text="–", bg=CARD, fg=INK, font=self.f_body).pack(side="left", padx=3)
        self._spin(drow, 0, 60, self.delay_max_var, 4, inc=0.5).pack(side="left")
        self._label(grid, "Max oldal / URL:").grid(row=1, column=2, sticky="w", pady=5)
        self.end_spin = self._spin(grid, 1, 1000000, self.end_page_var, 9)
        self.end_spin.grid(row=1, column=3, sticky="w", padx=(6, 0))

        checks = tk.Frame(st, bg=CARD)
        checks.pack(fill="x", pady=(8, 0))
        self._check(checks, "Összes oldal", self.all_pages_var, self._toggle_all_pages).pack(side="left", padx=(0, 18))
        self._check(checks, "Csak metaadat (fájl nélkül)", self.metadata_only_var).pack(side="left", padx=(0, 18))
        self._check(checks, "Rejtett böngésző", self.headless_var).pack(side="left")

        # --- Action buttons ----------------------------------------------
        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(0, 10))
        self.start_btn = self._button(actions, "▶  Letöltés indítása", self._start, GREEN, GREEN_DK)
        self.start_btn.pack(side="left")
        self.stop_btn = self._button(actions, "■  Leállítás", self._stop, RED, RED_DK)
        self.stop_btn.pack(side="left", padx=10)
        self.stop_btn.config(state="disabled")

        # --- Progress + status -------------------------------------------
        self.progress = ttk.Progressbar(body, mode="determinate",
                                         style="Sims.Horizontal.TProgressbar")
        self.progress.pack(fill="x")
        self.status_var = tk.StringVar(value="Készenlét. 🌿")
        tk.Label(body, textvariable=self.status_var, bg=BG, fg=GREEN_DK,
                 font=self.f_body, anchor="w").pack(fill="x", pady=(6, 8))

        # --- Log card (expands) ------------------------------------------
        logc = self._card(body, "📜 Napló", expand=True)
        self.log_text = tk.Text(logc, height=10, wrap="word", state="disabled",
                                font=self.f_mono, bg="#0F2417", fg="#CFF3D7",
                                relief="flat", padx=10, pady=8, insertbackground="#CFF3D7")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logc, command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=sb.set)

    def _apply_config(self) -> None:
        """Populate the controls from config.json (creators, URLs + optional
        defaults). Anything not present keeps the built-in widget default."""
        c = self._config

        creators = c.get("creators") or []
        if isinstance(creators, str):
            creators = [creators]
        creators = [str(x).strip() for x in creators if str(x).strip()]
        if creators:
            self.creator_text.delete("1.0", "end")
            self.creator_text.insert("1.0", "\n".join(creators))

        urls = config.entry_urls(c)
        if urls:
            self.url_text.delete("1.0", "end")
            self.url_text.insert("1.0", "\n".join(urls))

        def _set(key, var, cast):
            if key in c:
                try:
                    var.set(cast(c[key]))
                except (TypeError, ValueError):
                    pass

        _set("download_folder", self.folder_var, str)
        _set("workers", self.workers_var, int)
        _set("max_items", self.max_items_var, int)
        _set("end_page", self.end_page_var, int)
        _set("all_pages", self.all_pages_var, bool)
        _set("delay_min", self.delay_min_var, float)
        _set("delay_max", self.delay_max_var, float)
        _set("headless", self.headless_var, bool)
        _set("metadata_only", self.metadata_only_var, bool)
        self._toggle_all_pages()

    # -- small UI handlers -------------------------------------------------
    def _toggle_all_pages(self) -> None:
        self.end_spin.config(state="disabled" if self.all_pages_var.get() else "normal")

    def _pick_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.cwd()))
        if chosen:
            self.folder_var.set(chosen)

    def _log(self, msg: str) -> None:
        """Thread-safe: enqueue a log line (drained on the main thread)."""
        self.q.put(("log", msg))

    # -- start / stop ------------------------------------------------------
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        creators = [c.strip().lstrip("@") for c in
                    re.split(r"[\n,]+", self.creator_text.get("1.0", "end")) if c.strip()]
        url_text = self.url_text.get("1.0", "end")
        urls: list[str] = []
        if url_text.strip():
            try:
                urls = scraper.parse_entry_urls(url_text)
            except ScrapeError as exc:
                messagebox.showerror("Érvénytelen URL", str(exc))
                return
        if not creators and not urls:
            messagebox.showerror(
                "Mit töltsünk le?",
                "Adj meg legalább egy alkotót vagy egy linket.")
            return

        if not self.metadata_only_var.get() and not self._tos_acknowledged:
            if not messagebox.askyesno("Kérlek, olvasd el", TOS_NOTICE):
                return
            self._tos_acknowledged = True

        folder = Path(self.folder_var.get()).expanduser()
        delay = (float(self.delay_min_var.get()), float(self.delay_max_var.get()))
        if delay[0] > delay[1]:
            delay = (delay[1], delay[0])

        cfg = {
            "creators": creators,
            "urls": urls,
            "end_page": None if self.all_pages_var.get() else int(self.end_page_var.get()),
            "max_items": int(self.max_items_var.get()),
            "workers": max(1, int(self.workers_var.get())),
            "metadata_only": self.metadata_only_var.get(),
            "headless": self.headless_var.get(),
            "folder": folder,
            "delay": delay,
        }

        self.stop_event.clear()
        self._set_running(True)
        # Total is unknown with dynamic page-stepping: show a determinate bar only
        # when a max-item cap is set, otherwise animate an indeterminate bar.
        if cfg["max_items"]:
            self.progress.stop()
            self.progress.config(mode="determinate", maximum=cfg["max_items"], value=0)
        else:
            self.progress.config(mode="indeterminate", value=0)
            self.progress.start(12)
        self.worker = threading.Thread(target=self._run_job, args=(cfg,), daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("Leállítás… (aktuális elem befejezése)")

    def _set_running(self, running: bool) -> None:
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")

    # -- worker (background thread) ---------------------------------------
    def _run_job(self, cfg: dict) -> None:
        try:
            self._do_scrape(cfg)
        except Exception as exc:  # last-resort guard
            self._log(f"VÉGZETES HIBA: {exc}")
        finally:
            self.q.put(("done", None))

    def _do_scrape(self, cfg: dict) -> None:
        max_items = cfg["max_items"]
        folder = cfg["folder"]
        delay = cfg["delay"]
        metadata_only = cfg["metadata_only"]
        workers = cfg["workers"] if not metadata_only else 1

        # Verify Playwright/Chromium can launch once, before spawning N threads.
        if not metadata_only:
            from tsr.downloader import ensure_available, DownloaderUnavailable
            try:
                ensure_available(cfg["headless"])
            except DownloaderUnavailable as exc:
                self._log(f"! {exc}")
                self._log("Visszaváltás csak metaadat módra erre a futásra.")
                metadata_only = True
                workers = 1

        # Build the work list: resolve creator names to listing URLs, then add
        # any explicit URLs. Pages are stepped dynamically via the "#" placeholder.
        entries: list[str] = []
        if cfg["creators"]:
            self._log(f"{len(cfg['creators'])} alkotó feloldása…")
            for name in cfg["creators"]:
                if self.stop_event.is_set():
                    break
                tmpl = scraper.resolve_creator(name, on_log=self._log)
                if tmpl:
                    self._log(f"  ✓ {name}")
                    entries.append(tmpl)
                else:
                    self._log(f"  ✗ {name}: nem található ilyen alkotó (members/artists)")
        entries.extend(cfg["urls"])
        if not entries:
            self._log("Nincs feldolgozható forrás.")
            return

        max_pages = cfg["end_page"]  # None when "Összes oldal" is checked
        self._log(f"{len(entries)} forrás a sorban.")
        if not metadata_only:
            self._log(f"Párhuzamos letöltők: {workers}")
        if max_pages:
            self._log(f"Max oldal/URL: {max_pages}")

        # Shared state across producer + consumer threads.
        lock = threading.Lock()
        stats = {"scraped": 0, "processed": 0, "done": 0, "failed": 0, "skipped": 0}
        items: list = []
        manifest = storage.Manifest(folder) if not metadata_only else None
        reached_max = threading.Event()

        def push_status(title: str = "") -> None:
            with lock:
                shown = stats["scraped"] if metadata_only else stats["processed"]
                s = dict(stats)
            if max_items:  # determinate bar only when a cap is set
                self.q.put(("progress", (shown, max_items)))
            self.q.put((
                "status",
                f"feldolgozva={shown} · letöltve={s['done']} · hibás={s['failed']} "
                f"· kihagyva={s['skipped']}" + (f"  •  {title}" if title else ""),
            ))

        def save_metadata_snapshot() -> None:
            with lock:
                snapshot = list(items)
            if snapshot:
                storage.export_metadata(snapshot, folder)

        item_q: queue.Queue = queue.Queue(maxsize=max(4, workers * 4))

        # -- producer: scrape items, feed the download queue ---------------
        def produce() -> None:
            try:
                for idx, url in enumerate(entries, 1):
                    if self.stop_event.is_set() or reached_max.is_set():
                        break
                    self._log(f"[{idx}/{len(entries)}. forrás] {url}")
                    for item in scraper.iter_items(
                        url,
                        stop_event=self.stop_event,
                        on_log=self._log,
                        delay_range=delay,
                        max_pages=max_pages,
                    ):
                        if self.stop_event.is_set() or reached_max.is_set():
                            break
                        with lock:
                            items.append(item)
                            stats["scraped"] += 1
                            n = stats["scraped"]
                        if metadata_only:
                            push_status(f"{item.title} — {item.creator}")
                            if n % 10 == 0:
                                save_metadata_snapshot()
                        else:
                            # Block politely if consumers are saturated.
                            while not self.stop_event.is_set():
                                try:
                                    item_q.put(item, timeout=1)
                                    break
                                except queue.Full:
                                    continue
                        if max_items and n >= max_items:
                            reached_max.set()
                            break
            finally:
                # Unblock every consumer with a sentinel.
                for _ in range(workers):
                    try:
                        item_q.put_nowait(None)
                    except queue.Full:
                        try:
                            item_q.put(None, timeout=2)
                        except queue.Full:
                            pass

        # -- consumer: own browser, download items off the queue -----------
        def consume(wid: int) -> None:
            from tsr.downloader import TSRDownloader, DownloaderUnavailable
            try:
                with TSRDownloader(headless=cfg["headless"], on_log=self._log) as dl:
                    while not self.stop_event.is_set():
                        try:
                            item = item_q.get(timeout=1)
                        except queue.Empty:
                            continue
                        if item is None:
                            break
                        try:
                            existing = storage.existing_download(folder, item.id)
                            with lock:
                                already = manifest.is_done(item)
                                if existing and not already:
                                    # File present but not in manifest -> record it.
                                    manifest.mark(item, "done", existing.name)
                                    already = True
                            if already:
                                with lock:
                                    stats["skipped"] += 1
                                    stats["processed"] += 1
                                self._log(f"  kihagyva (fájl már létezik): {item.title}")
                            else:
                                path = dl.download(item, folder, self.stop_event)
                                with lock:
                                    if path:
                                        stats["done"] += 1
                                    else:
                                        stats["failed"] += 1
                                    stats["processed"] += 1
                                    manifest.mark(item, "done" if path else "failed",
                                                  path.name if path else None)
                                    snap = stats["processed"] % 10 == 0
                                if snap:
                                    save_metadata_snapshot()
                                time.sleep(random.uniform(*delay))
                            push_status(f"{item.title} — {item.creator}")
                        finally:
                            item_q.task_done()
            except DownloaderUnavailable as exc:
                self._log(f"  ! letöltő szál #{wid} nem indult: {exc}")
            except Exception as exc:  # keep one bad worker from killing the run
                self._log(f"  ! letöltő szál #{wid} hiba: {exc}")

        # -- orchestrate ---------------------------------------------------
        try:
            producer = threading.Thread(target=produce, name="scraper", daemon=True)
            producer.start()
            if metadata_only:
                producer.join()
            else:
                consumers = [
                    threading.Thread(target=consume, args=(i + 1,),
                                     name=f"dl-{i+1}", daemon=True)
                    for i in range(workers)
                ]
                for c in consumers:
                    c.start()
                producer.join()
                for c in consumers:
                    c.join()
            if reached_max.is_set():
                self._log(f"Elérve a max. elemszám ({max_items}).")
        finally:
            written = []
            with lock:
                if items:
                    written = storage.export_metadata(list(items), folder)
            for p in written:
                self._log(f"Mentve: {p}")

        with lock:
            s = dict(stats)
        self._log(
            f"Kész. elemek={s['scraped']} letöltve={s['done']} "
            f"hibás={s['failed']} kihagyva={s['skipped']}"
        )

    # -- queue draining (main thread) -------------------------------------
    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    cur, total = payload
                    self.progress.config(maximum=max(1, total), value=cur)
                elif kind == "done":
                    self._set_running(False)
                    self.progress.stop()
                    self.progress.config(mode="determinate")
                    self.progress.config(value=self.progress["maximum"])
                    self.status_var.set("Kész. ✅")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _append_log(self, msg: str) -> None:
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
