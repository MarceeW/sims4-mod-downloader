# TSR Sims 4 Mods Scraper

A Tkinter desktop app that browses
[thesimsresource.com](https://www.thesimsresource.com/downloads/browse/category/sims4-mods/),
walks the listing pages, and:

- **scrapes metadata** for every item (title, creator, downloads, file size,
  publish date, category, keywords, detail URL) → `metadata.json` + `metadata.csv`;
- optionally **downloads the free mod files** by driving the normal free-user
  download flow in a real browser (Playwright).

## How it works

Each browse page server-renders 20 items, and every item embeds a full JSON
record in a `div.item-wrapper[data-item]` attribute — so metadata needs just one
HTTP request per page (no per-item fetch).

Free files cannot be downloaded over plain HTTP for non-VIP users: the one-click
"Download Now" API is VIP-only, and the free path runs through a JavaScript +
ad-gated interstitial with a countdown. The downloader therefore uses Playwright
to reproduce the normal free-user flow — it **waits out the ad countdown** and
does **not** bypass any gate.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium      # only needed if you want to download files
python app.py
```

`tkinter` ships with the standard CPython installer on macOS/Windows. On some
Linux distros install it via the system package manager (e.g. `apt install python3-tk`).

## Usage

1. **Entry URLs** — paste one or more browse listings on `thesimsresource.com`,
   **one per line** (a different category like `.../category/sims4-clothing/`, or
   a mid-list page `.../page/5/`). Each entry is crawled across its pages in turn;
   the domain is locked to thesimsresource.com.
2. **End page** / **All pages** — how far to crawl from the entry page.
3. **Max items** — cap per run (`0` = no limit). Keep it small for a first test.
4. **Download folder** — where files + `metadata.*` + `manifest.json` are written.
5. **Metadata only** — leave checked to scrape without downloading files.
6. **Headless browser** — uncheck to watch the download flow while testing.
7. **Start** / **Stop**. Re-running resumes: items already downloaded (tracked in
   `manifest.json`) are skipped.

## Please note

`robots.txt` disallows the download endpoint, and bulk automated downloading may
conflict with TSR's Terms of Service. This tool keeps polite, randomized delays
and does not defeat ad-gating or the VIP gate. **You are responsible for how you
use it** — only download content you are entitled to.
