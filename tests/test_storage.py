"""Unit tests for SQLite storage persistence, category merging, and exports."""

import json
from pathlib import Path

from scraper.config import ScraperConfig
from scraper.models import BookRecord, BookStatus, DownloadResult
from scraper.storage import Storage


def test_storage_save_and_category_merge(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    storage = Storage(cfg)

    detail_url = "https://www.freebookcentre.net/ComputerScience-Books-Download/sample.html"

    # First occurrence in Category "Computer Science"
    book1 = BookRecord(
        stable_id="201",
        detail_url=detail_url,
        title="Sample Book",
        authors=["Alice Doe"],
        download_url="https://www.freebookcentre.net/gotoweb.php?id=201",
        categories=["Computer Science"],
    )
    storage.save_or_merge_book(book1)

    books = storage.get_all_books()
    assert len(books) == 1
    assert books[0].categories == ["Computer Science"]

    # Second occurrence in Category "Artificial Intelligence"
    book2 = BookRecord(
        stable_id="201",
        detail_url=detail_url,
        title="Sample Book",
        authors=["Alice Doe"],
        download_url="https://www.freebookcentre.net/gotoweb.php?id=201",
        categories=["Artificial Intelligence"],
    )
    storage.save_or_merge_book(book2)

    # Categories should be merged into ['Computer Science', 'Artificial Intelligence'] without duplicate rows
    books_after = storage.get_all_books()
    assert len(books_after) == 1
    assert set(books_after[0].categories) == {"Computer Science", "Artificial Intelligence"}


def test_storage_update_download_and_retry(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    storage = Storage(cfg)

    detail_url = "https://www.freebookcentre.net/book1.html"
    book = BookRecord(
        stable_id="202",
        detail_url=detail_url,
        title="Failing Book",
        download_url="https://www.freebookcentre.net/gotoweb.php?id=202",
    )
    storage.save_or_merge_book(book)

    # Update with failed result
    res = DownloadResult(status=BookStatus.FAILED, error="Connection timeout")
    storage.update_book_download(detail_url, res)

    # By default, retry_failed=False should not return it
    pending = storage.get_books_to_download(retry_failed=False)
    assert len(pending) == 0

    # With retry_failed=True, it should be included
    pending_retry = storage.get_books_to_download(retry_failed=True)
    assert len(pending_retry) == 1
    assert pending_retry[0].detail_url == detail_url


def test_storage_exports(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    storage = Storage(cfg)

    # Insert one downloaded book and one failed book
    b1 = BookRecord(
        stable_id="301",
        detail_url="https://www.freebookcentre.net/b1.html",
        title="Downloaded Book",
        status=BookStatus.DOWNLOADED,
        file_type="pdf",
        file_size=1024,
        sha256="abc123hash",
        local_path="ebooks/Cat/b1.pdf",
    )
    b2 = BookRecord(
        stable_id="302",
        detail_url="https://www.freebookcentre.net/b2.html",
        title="Failed Book",
        status=BookStatus.FAILED,
        error="404 Not Found",
    )
    storage.save_or_merge_book(b1)
    storage.save_or_merge_book(b2)

    json_path = storage.export_catalog_json()
    csv_path = storage.export_catalog_csv()
    failed_path = storage.export_failed_json()

    assert json_path.exists()
    assert csv_path.exists()
    assert failed_path.exists()

    with open(json_path, encoding="utf-8") as f:
        catalog_data = json.load(f)
    assert len(catalog_data) == 2

    with open(failed_path, encoding="utf-8") as f:
        failed_data = json.load(f)
    assert len(failed_data) == 1
    assert failed_data[0]["title"] == "Failed Book"
