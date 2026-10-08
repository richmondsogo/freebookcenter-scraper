"""CLI entry point and pipeline orchestrator for FreeBookCentre crawler/downloader."""

from __future__ import annotations

import argparse
import concurrent.futures
from pathlib import Path
import sys

from tqdm import tqdm

from scraper.config import ScraperConfig
from scraper.discovery import CatalogCrawler
from scraper.downloader import BookDownloader
from scraper.http_client import HttpClient
from scraper.models import BookRecord, BookStatus, CrawlStats, DownloadResult
from scraper.parser import parse_book_detail_page
from scraper.storage import Storage


def process_single_book(
    book: BookRecord,
    http_client: HttpClient,
    downloader: BookDownloader,
    storage: Storage,
    verbose: bool = False,
) -> DownloadResult:
    """
    Worker task:
    1. Fetch and parse book detail page (Schema.org metadata, author, pages, download link).
    2. Save enriched metadata.
    3. Resolve and stream download, validating magic bytes.
    4. Persist download result.
    """
    try:
        # Step 1: Fetch and parse book detail page
        status, html, _ = http_client.fetch_html(book.detail_url)
        if status != 200:
            result = DownloadResult(
                status=BookStatus.FAILED,
                error=f"HTTP {status} fetching detail page",
            )
            storage.update_book_download(book.detail_url, result)
            return result

        parsed_data = parse_book_detail_page(html, book.detail_url)

        # Merge parsed metadata into book record
        book.title = parsed_data.get("title") or book.title
        book.authors = parsed_data.get("authors") or book.authors
        book.description = parsed_data.get("description") or book.description
        book.pages = parsed_data.get("pages") or book.pages
        book.download_url = parsed_data.get("download_url") or book.download_url
        if parsed_data.get("categories"):
            combined_cats = list(dict.fromkeys(book.categories + parsed_data["categories"]))
            book.categories = combined_cats

        # Save enriched metadata before attempting download
        storage.save_or_merge_book(book)

        # Step 2: Download the resource
        if not book.download_url:
            result = DownloadResult(
                status=BookStatus.NO_DOWNLOAD_LINK,
                error="No download button or gotoweb link found on detail page",
            )
            storage.update_book_download(book.detail_url, result)
            return result

        result = downloader.download_book(book)
        storage.update_book_download(book.detail_url, result)

        if verbose and result.status == BookStatus.FAILED:
            print(f"\n[ERROR] Failed book {book.title}: {result.error}", file=sys.stderr)

        return result

    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)}"
        result = DownloadResult(status=BookStatus.FAILED, error=err)
        storage.update_book_download(book.detail_url, result)
        if verbose:
            print(f"\n[ERROR] Exception processing {book.detail_url}: {err}", file=sys.stderr)
        return result


def run_pipeline(config: ScraperConfig) -> CrawlStats:
    """Run discovery and download pipeline according to configuration."""
    print("=" * 60)
    print("   FreeBookCentre Crawler & Downloader")
    print(f"   Target:     {config.base_url}")
    print(f"   Output dir: {config.output_dir.resolve()}")
    print(f"   Workers:    {config.max_workers}")
    if config.smoke_test:
        print("   Mode:       Smoke test (limit = 10 books)")
    elif config.limit_books:
        print(f"   Mode:       Limited crawl ({config.limit_books} books)")
    else:
        print("   Mode:       Full catalog crawl")
    print("=" * 60)

    http_client = HttpClient(config)
    storage = Storage(config)
    downloader = BookDownloader(http_client, config)

    # -------------------------------------------------------------
    # Phase 1: Catalog Discovery
    # -------------------------------------------------------------
    if not config.download_only:
        crawler = CatalogCrawler(http_client, storage, config)
        print("\n[Phase 1] Discovering catalog structure and books...")

        # Count unvisited
        unvisited_before = len(storage.get_unvisited_listings())
        # If smoke test, limit discovery listings to find initial books quickly
        max_listings = 10 if config.smoke_test else None

        with tqdm(desc="Scanning catalog pages", unit="page") as pbar:
            def on_progress(url: str, books_found: int, crawled_total: int) -> None:
                pbar.update(1)
                pbar.set_postfix({"books": len(storage.get_all_books())})

            crawler.run_discovery(
                max_listings=max_listings,
                progress_callback=on_progress,
            )

        print(f"Discovery phase completed. Total books in catalog: {len(storage.get_all_books()):,}")

    if config.discovery_only:
        storage.export_catalog_json()
        storage.export_catalog_csv()
        stats = storage.get_stats()
        stats.print_summary()
        return stats

    # -------------------------------------------------------------
    # Phase 2: Detail Parsing & Downloading
    # -------------------------------------------------------------
    print("\n[Phase 2] Downloading and validating books...")
    limit = 10 if config.smoke_test else config.limit_books
    books_to_download = storage.get_books_to_download(
        retry_failed=config.retry_failed,
        limit=limit,
    )

    if not books_to_download:
        print("No pending books to download.")
    else:
        print(f"Processing {len(books_to_download):,} books using {config.max_workers} worker threads...")

        batch_counter = 0
        with tqdm(total=len(books_to_download), desc="Downloading", unit="book") as pbar:
            with concurrent.futures.ThreadPoolExecutor(max_workers=config.max_workers) as executor:
                future_to_book = {
                    executor.submit(
                        process_single_book,
                        book,
                        http_client,
                        downloader,
                        storage,
                        config.verbose,
                    ): book
                    for book in books_to_download
                }

                for future in concurrent.futures.as_completed(future_to_book):
                    res = future.result()
                    pbar.update(1)
                    batch_counter += 1

                    # Incrementally sync catalog every 20 books
                    if batch_counter % 20 == 0:
                        storage.export_catalog_json()
                        storage.export_catalog_csv()

    # -------------------------------------------------------------
    # Phase 3: Final Catalog Exports & Reporting
    # -------------------------------------------------------------
    print("\n[Phase 3] Generating final reports and metadata catalogs...")
    json_path = storage.export_catalog_json()
    csv_path = storage.export_catalog_csv()
    failed_path = storage.export_failed_json()

    print(f"-> Catalog JSON saved: {json_path}")
    print(f"-> Catalog CSV saved:  {csv_path}")
    print(f"-> Failures JSON saved: {failed_path}")

    stats = storage.get_stats()
    stats.print_summary()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FreeBookCentre robust catalog crawler and book downloader."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("ebooks"),
        help="Directory to store downloaded ebooks and catalog metadata (default: ebooks)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent worker threads (default: 4)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of books to download in this run",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run a small smoke test (discovers a few categories, downloads up to 10 books)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Retry previously failed downloads",
    )
    parser.add_argument(
        "--discovery-only",
        action="store_true",
        help="Only crawl and discover books, do not download files",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Skip catalog discovery and download already discovered books in database",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="Export catalog.json and catalog.csv from current database without crawling",
    )
    parser.add_argument(
        "--convert-html",
        action="store_true",
        help="Convert view-only HTML web books into high-fidelity PDFs using headless Chromium",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed warning/error logging",
    )

    args = parser.parse_args()

    config = ScraperConfig(
        output_dir=args.output_dir,
        max_workers=args.workers,
        limit_books=args.limit,
        smoke_test=args.smoke_test,
        retry_failed=args.retry_failed,
        discovery_only=args.discovery_only,
        download_only=args.download_only,
        verbose=args.verbose,
    )

    if args.export_only:
        storage = Storage(config)
        j = storage.export_catalog_json()
        c = storage.export_catalog_csv()
        f = storage.export_failed_json()
        print(f"Exported: {j}, {c}, {f}")
        return

    if args.convert_html:
        from scraper.html_converter import HtmlToPdfConverter
        storage = Storage(config)
        converter = HtmlToPdfConverter(storage, config)
        converter.run(limit=args.limit, retry_failed=args.retry_failed)
        return

    run_pipeline(config)


if __name__ == "__main__":
    main()
