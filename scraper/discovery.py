"""Graph-based discovery crawler for FreeBookCentre catalog hierarchy."""

from __future__ import annotations

import collections
from typing import Callable
from urllib.parse import urlparse

from scraper.config import ScraperConfig
from scraper.http_client import HttpClient
from scraper.models import BookRecord, BookStatus
from scraper.parser import (
    canonicalize_url,
    clean_category_name,
    is_book_detail_url,
    parse_listing_page,
)
from scraper.storage import Storage


class CatalogCrawler:
    """Discovers all categories, subcategories, topic pages, and book detail URLs."""

    def __init__(
        self,
        http_client: HttpClient,
        storage: Storage,
        config: ScraperConfig | None = None,
    ) -> None:
        self.http = http_client
        self.storage = storage
        self.config = config or ScraperConfig()

    def initialize_seeds(self) -> None:
        """Add seed URLs if not already populated."""
        self.storage.add_crawl_url(self.config.base_url, "listing")
        self.storage.add_crawl_url(self.config.sitemap_url, "listing")

    def run_discovery(
        self,
        max_listings: int | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> int:
        """
        Walk listing pages in a BFS queue until no unvisited listings remain
        or max_listings is reached.
        Returns:
            Number of listing pages crawled in this run.
        """
        self.initialize_seeds()

        crawled_count = 0
        while True:
            unvisited = self.storage.get_unvisited_listings(limit=50)
            if not unvisited:
                break

            for listing_url in unvisited:
                if max_listings and crawled_count >= max_listings:
                    return crawled_count

                self.storage.mark_url_visited(listing_url)
                crawled_count += 1

                try:
                    status, html, _ = self.http.fetch_html(listing_url)
                    if status != 200:
                        continue

                    book_urls, sub_listings = parse_listing_page(html, listing_url)

                    # Extract category name from URL or title for context
                    path_parts = [p for p in urlparse(listing_url).path.split("/") if p]
                    category_hint = clean_category_name(path_parts[0]) if path_parts else "General"

                    # Save discovered books
                    for b_url in book_urls:
                        # Derive initial stable ID from URL slug
                        slug = b_url.split("/")[-1].replace(".html", "").replace(".htm", "")
                        book = BookRecord(
                            stable_id=slug,
                            detail_url=b_url,
                            title=slug.replace("-", " "),
                            categories=[category_hint] if category_hint else [],
                            status=BookStatus.DISCOVERED,
                        )
                        self.storage.save_or_merge_book(book)

                    # Queue new sub-listing URLs
                    for sub_url in sub_listings:
                        self.storage.add_crawl_url(sub_url, "listing")

                    if progress_callback:
                        progress_callback(listing_url, len(book_urls), crawled_count)

                except Exception as exc:
                    # Never let one broken listing crash discovery
                    if self.config.verbose:
                        print(f"[WARN] Failed to crawl listing {listing_url}: {exc}")

        return crawled_count
