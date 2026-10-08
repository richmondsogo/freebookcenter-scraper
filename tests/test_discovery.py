"""Unit tests for catalog graph discovery crawler."""

from pathlib import Path
from unittest.mock import MagicMock

from scraper.config import ScraperConfig
from scraper.discovery import CatalogCrawler
from scraper.storage import Storage
from tests.fixtures import SAMPLE_CATEGORY_LISTING_HTML


def test_catalog_crawler_discovery(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    storage = Storage(cfg)
    mock_http = MagicMock()

    # When crawler requests base_url, return listing with 2 books and 2 sub-categories
    mock_http.fetch_html.return_value = (200, SAMPLE_CATEGORY_LISTING_HTML, {})

    crawler = CatalogCrawler(mock_http, storage, cfg)
    crawled = crawler.run_discovery(max_listings=1)

    assert crawled == 1

    # Verify discovered books were recorded in storage
    books = storage.get_all_books()
    assert len(books) == 2
    book_urls = [b.detail_url for b in books]
    assert (
        "https://www.freebookcentre.net/ComputerScience-Books-Download/Introduction-to-Artificial-Intelligence-by-Marc-Toussaint.html"
        in book_urls
    )

    # Verify unvisited sub-listings were queued
    unvisited = storage.get_unvisited_listings()
    assert len(unvisited) >= 2
