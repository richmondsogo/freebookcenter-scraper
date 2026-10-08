"""Convert view-only HTML web books into high-fidelity PDFs using headless Chromium."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Browser, Page
from tqdm import tqdm

from scraper.config import ScraperConfig
from scraper.models import BookRecord, BookStatus, DownloadResult
from scraper.parser import determine_local_filepath
from scraper.storage import Storage


def find_embedded_pdf_url(html: str, base_url: str) -> str | None:
    """
    Check if the HTML page contains a direct download link to an original PDF
    (e.g., Internet Archive, repository download links).
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            # Prioritize direct PDF links from archive.org or repository downloads
            if "archive.org/download/" in href and href.lower().endswith(".pdf"):
                return urljoin(base_url, href)
            if href.lower().endswith(".pdf") and any(
                k in href.lower() for k in ["download", "fulltext", "book", "pdf"]
            ):
                return urljoin(base_url, href)
    except Exception:
        pass
    return None


class HtmlToPdfConverter:
    """Renders HTML web pages into organized, high-quality PDFs using Playwright."""

    def __init__(self, storage: Storage, config: ScraperConfig | None = None) -> None:
        self.storage = storage
        self.config = config or ScraperConfig()

    def process_book(self, page: Page, book: BookRecord) -> DownloadResult:
        """Render a single view-only book webpage into a PDF."""
        url = book.resolved_url or book.download_url or book.detail_url
        if not url:
            return DownloadResult(status=BookStatus.FAILED, error="No valid URL for HTML book")

        part_path: Path | None = None
        try:
            target_path = determine_local_filepath(
                output_dir=self.config.output_dir,
                categories=book.categories,
                title=book.title,
                stable_id=book.stable_id,
                extension="pdf",
            )

            # Skip if already exists and valid
            if target_path.exists() and target_path.stat().st_size > 1000:
                size = target_path.stat().st_size
                hasher = hashlib.sha256()
                with open(target_path, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        hasher.update(chunk)
                return DownloadResult(
                    status=BookStatus.DOWNLOADED,
                    local_path=str(target_path),
                    file_type="pdf",
                    file_size=size,
                    sha256=hasher.hexdigest(),
                    resolved_url=url,
                )

            part_path = target_path.with_name(f"{target_path.name}.part")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                part_path.unlink(missing_ok=True)
            except Exception:
                pass

            # Navigate to webpage with reasonable timeout
            response = page.goto(
                url,
                timeout=35000,
                wait_until="domcontentloaded",
            )

            # Check if an original PDF link is embedded on the page
            try:
                page_html = page.content()
                direct_pdf = find_embedded_pdf_url(page_html, url)
                if direct_pdf:
                    # Attempt quick direct download of original PDF
                    r = requests.get(
                        direct_pdf,
                        stream=True,
                        timeout=30,
                        headers={"User-Agent": self.config.user_agent},
                    )
                    if r.status_code == 200:
                        first_chunk = next(r.iter_content(1024), b"")
                        if first_chunk.startswith(b"%PDF-"):
                            hasher = hashlib.sha256()
                            bytes_written = len(first_chunk)
                            with open(part_path, "wb") as f_out:
                                f_out.write(first_chunk)
                                hasher.update(first_chunk)
                                for chunk in r.iter_content(65536):
                                    if chunk:
                                        f_out.write(chunk)
                                        hasher.update(chunk)
                                        bytes_written += len(chunk)
                            os.replace(part_path, target_path)
                            return DownloadResult(
                                status=BookStatus.DOWNLOADED,
                                local_path=str(target_path),
                                file_type="pdf",
                                file_size=bytes_written,
                                sha256=hasher.hexdigest(),
                                resolved_url=direct_pdf,
                            )
            except Exception:
                pass

            # Emulate screen styles for rich layouts and colors
            page.emulate_media(media="screen")

            # Render entire page to PDF
            page.pdf(
                path=str(part_path),
                format="A4",
                print_background=True,
                margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"},
            )

            if not part_path.exists() or part_path.stat().st_size == 0:
                part_path.unlink(missing_ok=True)
                return DownloadResult(status=BookStatus.FAILED, error="0 bytes generated by Chromium")

            # Validate %PDF- header
            with open(part_path, "rb") as f:
                header = f.read(10)
            if not header.startswith(b"%PDF-"):
                part_path.unlink(missing_ok=True)
                return DownloadResult(status=BookStatus.FAILED, error="Invalid PDF header generated")

            # Atomic rename
            os.replace(part_path, target_path)
            size = target_path.stat().st_size

            hasher = hashlib.sha256()
            with open(target_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)

            return DownloadResult(
                status=BookStatus.DOWNLOADED,
                local_path=str(target_path),
                file_type="pdf",
                file_size=size,
                sha256=hasher.hexdigest(),
                resolved_url=url,
            )

        except Exception as exc:
            if part_path is not None:
                try:
                    part_path.unlink(missing_ok=True)
                except Exception:
                    pass
            err_msg = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
            return DownloadResult(status=BookStatus.FAILED, error=err_msg)

    def run(self, limit: int | None = None, retry_failed: bool = False) -> None:
        """Convert pending view_only_html books to PDF."""
        # Query view_only_html books (and optionally failed ones)
        conn = self.storage._get_connection()
        if retry_failed:
            query = (
                "SELECT * FROM books WHERE status = 'view_only_html' "
                "OR (status = 'failed' AND (file_type = 'html' OR error LIKE '%Chromium%' OR error LIKE '%Page%' OR error LIKE '%Timeout%')) "
                "ORDER BY title ASC"
            )
        else:
            query = "SELECT * FROM books WHERE status = 'view_only_html' ORDER BY title ASC"
        if limit:
            query += f" LIMIT {limit}"
        rows = conn.execute(query).fetchall()
        conn.close()

        books = [BookRecord.from_dict(dict(r)) for r in rows]
        if not books:
            print("No 'view_only_html' books found to convert.")
            return

        print("=" * 60)
        print("   HTML-to-PDF Web Page Capture")
        print(f"   Target:  {len(books):,} view-only web books")
        print(f"   Output:  {self.config.output_dir.resolve()}")
        print("=" * 60)

        converted_count = 0
        failed_count = 0

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-web-security", "--disable-features=IsolateOrigins,site-per-process"],
            )
            # Create a reusable browser context
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=self.config.user_agent,
            )
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.dismiss())

            with tqdm(total=len(books), desc="Converting web pages to PDF", unit="book") as pbar:
                for idx, book in enumerate(books):
                    try:
                        if not browser.is_connected():
                            browser = p.chromium.launch(
                                headless=True,
                                args=["--disable-web-security", "--disable-features=IsolateOrigins,site-per-process"],
                            )
                            context = browser.new_context(
                                viewport={"width": 1280, "height": 900},
                                user_agent=self.config.user_agent,
                            )
                            page = context.new_page()
                            page.on("dialog", lambda dialog: dialog.dismiss())
                        elif page.is_closed():
                            page = context.new_page()
                            page.on("dialog", lambda dialog: dialog.dismiss())

                        result = self.process_book(page, book)
                    except Exception as loop_exc:
                        result = DownloadResult(
                            status=BookStatus.FAILED,
                            error=f"{type(loop_exc).__name__}: {str(loop_exc).splitlines()[0]}"
                        )

                    if result.status == BookStatus.DOWNLOADED:
                        converted_count += 1
                        self.storage.update_book_download(book.detail_url, result)
                    else:
                        failed_count += 1
                        self.storage.update_book_download(book.detail_url, result)
                        if self.config.verbose:
                            print(f"\n[WARN] Failed {book.title}: {result.error}", file=sys.stderr)

                    pbar.update(1)
                    pbar.set_postfix({"saved": converted_count, "failed": failed_count})

                    # Sync catalog every 25 books
                    if (idx + 1) % 25 == 0:
                        self.storage.export_catalog_json()
                        self.storage.export_catalog_csv()

                    # Recycle browser context every 50 books to prevent memory leaks
                    if (idx + 1) % 50 == 0:
                        try:
                            context.close()
                        except Exception:
                            pass
                        context = browser.new_context(
                            viewport={"width": 1280, "height": 900},
                            user_agent=self.config.user_agent,
                        )
                        page = context.new_page()
                        page.on("dialog", lambda dialog: dialog.dismiss())

            try:
                context.close()
                browser.close()
            except Exception:
                pass

        # Final exports
        self.storage.export_catalog_json()
        self.storage.export_catalog_csv()
        self.storage.export_failed_json()

        print("\n" + "=" * 48)
        print("          HTML CONVERSION SUMMARY               ")
        print("=" * 48)
        print(f"Total processed:    {len(books):,}")
        print(f"Saved as PDF:       {converted_count:,}")
        print(f"Failed / Timed out: {failed_count:,}")
        print("=" * 48 + "\n")
