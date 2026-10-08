# FreeBookCentre Crawler & Downloader

Production-quality crawler, parser, and downloader for [FreeBookCentre.net](https://www.freebookcentre.net/).

---

## 1. Actual Site Structure Discovered

Through live inspection of the website DOM, HTTP response streams, and server behavior, the actual site structure was determined:

```
Homepage / Sitemap
      │
      ▼
Category / Topic Listing Pages (e.g., /CompuScience/Free-Artificial-Intelligence-Books-Download.html)
      │
      ▼
Individual Book Detail Pages (e.g., /ComputerScience-Books-Download/Introduction-to-Artificial-Intelligence...html)
      │
      ▼  (Extract Schema.org Book metadata, breadcrumbs, and gotoweb.php?id=...)
Download Resolver (/gotoweb.php?id=10748)
      │
      ├── [HTTP 302 Redirect] ──► Direct Binary File (PDF / EPUB / ZIP) ──► Validated & Downloaded
      └── [HTTP 302 Redirect] ──► Web Viewer / Documentation Page       ──► Recorded as view_only_html
```

### Key Discoveries:
1. **Listing Pages vs. Detail Pages**:
   - Topic pages (e.g. `Free-Artificial-Intelligence-Books-Download.html`) list multiple books and do *not* contain download links.
   - Book links on topic pages follow the pattern `/<Section>[-_][bB]ooks[-_][dD]ownload/<Book-Slug>.html`.
2. **Book Detail Pages**:
   - Contain rich **Schema.org** markup: `<div itemscope itemtype="http://schema.org/Book">` with `name`, `author`, `description`, and `numberOfPages`.
   - Feature breadcrumb navigation (`<div class="brd-txt">`) used for hierarchical folder categorization.
   - Feature a download button pointing to an internal resolver: `/gotoweb.php?id=<ID>`.
3. **Download Resolution (`gotoweb.php`)**:
   - The resolver responds with an `HTTP 302` redirect to the actual hosting server (universities, publishers, open repositories).
   - Resources fall into two categories:
     - **Direct downloadable files** (`application/pdf`, `%PDF-` binary magic bytes).
     - **View-only web pages** (`text/html`, online reading chapters, e.g., IntechOpen, Princeton, FreeBSD docs).
   - Our downloader inspects the initial byte stream: PDFs are streamed to `.part` files, validated, and atomically saved; HTML web viewers are detected, preventing saving fake PDFs, and logged with their destination URLs.

---

## 2. Directory Structure

```
freebookcenter-scraper/
├── scraper/
│   ├── __init__.py
│   ├── config.py         # Config options, timeouts, concurrency, rate limits
│   ├── models.py         # BookRecord, DownloadResult, CrawlStats dataclasses
│   ├── http_client.py    # Thread-safe sessions, retry adapter, polite delays, SSL fallback
│   ├── parser.py         # URL canonicalization, Schema.org parsing, Windows filename sanitization
│   ├── discovery.py      # Graph crawler for category listings and books
│   ├── downloader.py     # Stream validation (%PDF- signature), atomic .part rename, SHA-256
│   ├── storage.py        # SQLite persistent state (WAL mode) with atomic JSON/CSV exports
│   └── main.py           # CLI interface and pipeline orchestrator
├── scraper.py            # Root runner script
├── tests/                # Unit test suite with representative HTML fixtures
│   ├── conftest.py
│   ├── fixtures.py
│   ├── test_parser.py
│   ├── test_downloader.py
│   ├── test_storage.py
│   └── test_discovery.py
└── ebooks/               # Output directory
    ├── crawler_state.db  # SQLite database tracking crawl progress and metadata
    ├── catalog.json      # Machine-readable JSON catalog of all discovered books
    ├── catalog.csv       # Flat CSV catalog
    ├── failed.json       # Record of unresolved/dead links for retry
    └── <Category>/
        └── <SubCategory>/
            └── <Book Title> [<stable_id>].pdf
```

---

## 3. Usage & CLI Options

### A. Run a Quick Smoke Test (10 books)
```bash
python scraper.py --smoke-test --verbose
```

### B. Run the Full Catalog Crawl
To systematically crawl the entire website and download all available books:
```bash
python scraper.py --workers 4
```

### C. Resume an Interrupted Crawl
The scraper is completely resumable out of the box:
- Already downloaded files are verified by magic bytes and size, then skipped.
- Previously discovered URLs are persisted in `crawler_state.db`.
Simply run:
```bash
python scraper.py
```

### D. Download Only (Skip Catalog Discovery)
If the catalog has already been discovered and you want to download pending books:
```bash
python scraper.py --download-only
```

### E. Retry Failed Downloads
To retry dead links or temporarily unavailable external hosts:
```bash
python scraper.py --retry-failed
```

### F. Re-export Catalogs
To generate fresh `catalog.json` and `catalog.csv` from the database without crawling:
```bash
python scraper.py --export-only
```

### G. Save View-Only HTML Web Pages as PDFs
To preserve knowledge from view-only web pages / online books:
- Uses headless Chromium via Playwright.
- Emulates high-fidelity screen CSS styling (retaining syntax highlighting, background colors, charts, diagrams).
- Automatically checks for original source PDF links (e.g. Internet Archive) before rendering.
- Saves valid PDFs into `ebooks/<Category>/<Subcategory>/<Title> [<id>].pdf`, validates `%PDF-` signature, and updates the database.

Run the full HTML conversion:
```bash
python scraper.py --convert-html
```

Or test with a batch limit:
```bash
python scraper.py --convert-html --limit 50
```

To retry any previously timed-out or failed web pages:
```bash
python scraper.py --convert-html --retry-failed
```

---

## 4. Running Tests
Run the comprehensive pytest suite:
```bash
python -m pytest -v
```

All 21 unit tests cover:
- URL canonicalization
- Windows filename & folder sanitization
- Duplicate detection & category merging
- Schema.org book parsing & breadcrumbs
- Download link resolution
- File signature validation (`%PDF-`, `PK\x03\x04`)
- HTML vs. PDF detection
- Embedded original PDF discovery
- HTML-to-PDF Chromium rendering & part cleanup
- Interrupted `.part` download cleanup
- SQLite persistence, retry logic & catalog exports
