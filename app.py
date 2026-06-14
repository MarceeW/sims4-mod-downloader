#!/usr/bin/env python3
"""Tkinter GUI for the TSR Sims 4 free-mods scraper / downloader.

Run:  python app.py

The scraping + (Playwright) download work runs on a background thread; the GUI
polls a thread-safe queue for log/progress updates so the window stays
responsive and Tk widgets are only touched from the main thread.
"""

from __future__ import annotations

import queue
import random
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tsr import ITEMS_PER_PAGE, scraper, storage
from tsr.scraper import ScrapeError

DEFAULT_URL = "https://www.thesimsresource.com/downloads/browse/category/sims4-mods/"

TOS_NOTICE = (
    "Ez az eszköz a thesimsresource.com normál, ingyenes letöltési folyamatát "
    "automatizálja (kivárja a hirdetés-visszaszámlálót, és nem kerül meg semmilyen "
    "korlátozást).\n\n"
    "Megjegyzés: az oldal robots.txt-je tiltja a letöltési végpontot, és a tömeges "
    "automatizált letöltés ütközhet a TSR felhasználási feltételeivel. A használat "
    "a te felelősséged. Kérlek, tartsd a késleltetést kíméletesen, és csak olyan "
    "tartalmat tölts le, amire jogosult vagy.\n\nFolytatod?"
)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("TSR Sims 4 Mod Letöltő")
        root.geometry("760x640")
        root.minsize(640, 520)

        self.q: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_event = threading.Event()
        self._tos_acknowledged = False

        self._build_ui()
        self.root.after(100, self._poll_queue)

    # -- UI construction ---------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(frm, text="Belépő URL-ek\n(soronként egy):").grid(row=row, column=0, sticky="nw", **pad)
        self.url_text = tk.Text(frm, height=3, wrap="none")
        self.url_text.insert("1.0", DEFAULT_URL)
        self.url_text.grid(row=row, column=1, columnspan=2, sticky="ew", **pad)

        row += 1
        ttk.Label(frm, text="Letöltési mappa:").grid(row=row, column=0, sticky="w", **pad)
        self.folder_var = tk.StringVar(value=str(Path.cwd() / "downloads"))
        ttk.Entry(frm, textvariable=self.folder_var).grid(row=row, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Tallózás…", command=self._pick_folder).grid(row=row, column=2, **pad)

        row += 1
        opts = ttk.Frame(frm)
        opts.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)

        ttk.Label(opts, text="Utolsó oldal:").pack(side="left")
        self.end_page_var = tk.IntVar(value=1)
        self.end_spin = ttk.Spinbox(opts, from_=1, to=100000, width=7, textvariable=self.end_page_var)
        self.end_spin.pack(side="left", padx=(2, 12))

        self.all_pages_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Összes oldal", variable=self.all_pages_var, command=self._toggle_all_pages
        ).pack(side="left", padx=(0, 12))

        ttk.Label(opts, text="Max. elem (0=∞):").pack(side="left")
        self.max_items_var = tk.IntVar(value=5)
        ttk.Spinbox(opts, from_=0, to=100000, width=7, textvariable=self.max_items_var).pack(
            side="left", padx=(2, 12)
        )

        ttk.Label(opts, text="Párhuzamos letöltés:").pack(side="left")
        self.workers_var = tk.IntVar(value=3)
        ttk.Spinbox(opts, from_=1, to=8, width=4, textvariable=self.workers_var).pack(
            side="left", padx=(2, 12)
        )

        row += 1
        opts2 = ttk.Frame(frm)
        opts2.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)

        ttk.Label(opts2, text="Késleltetés min/max (mp):").pack(side="left")
        self.delay_min_var = tk.DoubleVar(value=2.0)
        self.delay_max_var = tk.DoubleVar(value=4.0)
        ttk.Spinbox(opts2, from_=0, to=60, increment=0.5, width=5, textvariable=self.delay_min_var).pack(side="left", padx=2)
        ttk.Spinbox(opts2, from_=0, to=60, increment=0.5, width=5, textvariable=self.delay_max_var).pack(side="left", padx=(2, 12))

        self.metadata_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts2, text="Csak metaadat (fájl nélkül)", variable=self.metadata_only_var).pack(side="left", padx=(0, 12))
        self.headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts2, text="Rejtett böngésző", variable=self.headless_var).pack(side="left")

        row += 1
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)
        self.start_btn = ttk.Button(btns, text="Indítás", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Leállítás", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)

        row += 1
        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=row, column=0, columnspan=3, sticky="ew", **pad)

        row += 1
        self.status_var = tk.StringVar(value="Készenlét.")
        ttk.Label(frm, textvariable=self.status_var).grid(row=row, column=0, columnspan=3, sticky="w", **pad)

        row += 1
        frm.rowconfigure(row, weight=1)
        log_frame = ttk.Frame(frm)
        log_frame.grid(row=row, column=0, columnspan=3, sticky="nsew", **pad)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=14, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=sb.set)

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

        try:
            entries = scraper.parse_entry_urls(self.url_text.get("1.0", "end"))
        except ScrapeError as exc:
            messagebox.showerror("Érvénytelen URL", str(exc))
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
            "entries": entries,
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
        self.progress.config(value=0, maximum=100)
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
        entries = cfg["entries"]
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

        # Resolve the page range for every entry up front so we can show a real
        # progress bar (estimated total = pages * items-per-page, capped by
        # max_items if set).
        self._log(f"{len(entries)} belépő URL a sorban.")
        plan: list[tuple[str, int, int]] = []
        for idx, (category, start) in enumerate(entries, 1):
            if self.stop_event.is_set():
                break
            end = cfg["end_page"]
            if end is None:
                self._log(f"Oldalszám lekérése: '{category}'…")
                end = scraper.get_total_pages(category)
            end = max(start, end)
            plan.append((category, start, end))
            self._log(f"[{idx}/{len(entries)}. URL] '{category}', oldalak {start}…{end}.")

        estimated = sum((end - start + 1) * ITEMS_PER_PAGE for _, start, end in plan)
        if max_items:
            estimated = min(estimated, max_items) if estimated else max_items
        total = max(1, estimated)
        self._log(f"Becsült összes elem: ~{estimated}")
        if not metadata_only:
            self._log(f"Párhuzamos letöltők: {workers}")

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
            self.q.put(("progress", (shown, total)))
            self.q.put((
                "status",
                f"{shown}/~{estimated} • letöltve={s['done']} hibás={s['failed']} "
                f"kihagyva={s['skipped']}" + (f" • {title}" if title else ""),
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
                for category, start, end in plan:
                    if self.stop_event.is_set() or reached_max.is_set():
                        break
                    for item in scraper.iter_items(
                        category, start, end,
                        stop_event=self.stop_event,
                        on_log=self._log,
                        delay_range=delay,
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
                            with lock:
                                already = manifest.is_done(item)
                            if already:
                                with lock:
                                    stats["skipped"] += 1
                                    stats["processed"] += 1
                                self._log(f"  kihagyva (már letöltve): {item.title}")
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
                    self.progress.config(value=self.progress["maximum"])
                    self.status_var.set("Kész.")
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
