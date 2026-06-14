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
   the domain is locked to thesimsresource.com. The field is **pre-filled from
   `config.json`** (see below) — there is no hard-coded default URL.
2. **End page** / **All pages** — how far to crawl from the entry page.
3. **Max items** — cap per run (`0` = no limit). Keep it small for a first test.
4. **Download folder** — where files + `metadata.*` + `manifest.json` are written.
5. **Metadata only** — leave checked to scrape without downloading files.
6. **Headless browser** — uncheck to watch the download flow while testing.
7. **Start** / **Stop**. Re-running resumes: items already downloaded (tracked in
   `manifest.json`) are skipped.

## Configuration (`config.json`)

The download URLs and default settings are read from `config.json` at startup
(next to the executable when packaged, otherwise the working directory). Edit it
to set which links to download — no URL is hard-coded in the app:

```json
{
  "entry_urls": [
    "https://www.thesimsresource.com/downloads/browse/category/sims4-mods/",
    "https://www.thesimsresource.com/downloads/browse/category/sims4-clothing/"
  ],
  "download_folder": "downloads",
  "workers": 3,
  "delay_min": 2.0,
  "delay_max": 4.0,
  "headless": true,
  "metadata_only": false
}
```

Only `entry_urls` is required; every other key is optional and falls back to the
GUI default if omitted. If `config.json` is missing, the URL field starts empty
and you paste links manually.

### URL schemes and the `#` page placeholder

Any `thesimsresource.com` listing URL works — category browse, a creator's
`/members/<name>/...` page, or an `/artists/<name>/...` page. Put a **`#`** where
the page number goes; the app substitutes `1, 2, 3 …` and **steps forward until
the listing runs out** (it detects when a page repeats, since the site clamps
overflow pages to the last one). Examples:

```
https://www.thesimsresource.com/downloads/browse/category/sims4-objects/page/#
https://www.thesimsresource.com/members/VentaStudio/downloads/browse/category/sims4/page/#
https://www.thesimsresource.com/artists/SIMcredible!/downloads/browse/category/sims4/skipsetitems/1/page/#
```

- The `#` must sit at the real page position — use `.../page/#`, **not** a bare
  `.../<category>/#` (a trailing number after the category is ignored by the
  site, so only page 1 would be fetched).
- A URL without `#` is fetched once (single page).
- There is **no limit** on the number of entry URLs or on parallel workers
  (mind the rate-limit/resource trade-off described above).
- "Összes oldal" steps every page; uncheck it and set "Max oldal/URL" to cap
  pages per URL (handy for testing).

## Please note

`robots.txt` disallows the download endpoint, and bulk automated downloading may
conflict with TSR's Terms of Service. This tool keeps polite, randomized delays
and does not defeat ad-gating or the VIP gate. **You are responsible for how you
use it** — only download content you are entitled to.
