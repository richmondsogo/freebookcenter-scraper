"""Unit tests for downloader, magic bytes validation, and atomic filesystem storage."""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scraper.config import ScraperConfig
from scraper.downloader import BookDownloader, detect_file_type
from scraper.models import BookRecord, BookStatus
from tests.fixtures import SAMPLE_HTML_VIEWER_PAGE


def test_detect_file_type_pdf():
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<\n>>"
    ext, is_html = detect_file_type(pdf_bytes, "application/pdf")
    assert ext == "pdf"
    assert is_html is False


def test_detect_file_type_html():
    html_bytes = SAMPLE_HTML_VIEWER_PAGE.encode("utf-8")
    ext, is_html = detect_file_type(html_bytes, "text/html; charset=utf-8")
    assert is_html is True
    assert ext is None


def test_detect_file_type_zip():
    zip_bytes = b"PK\x03\x04\x14\x00\x00\x00"
    ext, is_html = detect_file_type(zip_bytes, "application/zip")
    assert ext == "zip"
    assert is_html is False


def test_download_valid_pdf(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    mock_http = MagicMock()

    pdf_content = b"%PDF-1.5 sample content for testing"
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.url = "http://example.com/test.pdf"
    mock_response.headers = {
        "Content-Type": "application/pdf",
        "Content-Length": str(len(pdf_content)),
    }
    mock_response.iter_content.return_value = [pdf_content]
    mock_http.get_stream.return_value = mock_response

    downloader = BookDownloader(mock_http, cfg)
    book = BookRecord(
        stable_id="101",
        detail_url="https://www.freebookcentre.net/test.html",
        title="Test Book",
        download_url="https://www.freebookcentre.net/gotoweb.php?id=101",
        categories=["Computer Science"],
    )

    res = downloader.download_book(book)

    assert res.status == BookStatus.DOWNLOADED
    assert res.file_type == "pdf"
    assert res.file_size == len(pdf_content)
    assert res.sha256 == hashlib.sha256(pdf_content).hexdigest()
    assert Path(res.local_path).exists()
    assert Path(res.local_path).read_bytes() == pdf_content
    # Ensure no .part file was left behind
    assert not Path(res.local_path + ".part").exists()


def test_download_html_viewer_rejected(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    mock_http = MagicMock()

    html_content = SAMPLE_HTML_VIEWER_PAGE.encode("utf-8")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.url = "https://www.intechopen.com/books/viewer"
    mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
    mock_response.iter_content.return_value = [html_content]
    mock_http.get_stream.return_value = mock_response

    downloader = BookDownloader(mock_http, cfg)
    book = BookRecord(
        stable_id="102",
        detail_url="https://www.freebookcentre.net/viewer.html",
        title="HTML Book",
        download_url="https://www.freebookcentre.net/gotoweb.php?id=102",
        categories=["AI"],
    )

    res = downloader.download_book(book)

    assert res.status == BookStatus.VIEW_ONLY_HTML
    assert "HTML page/viewer" in res.error
    # Must NOT save any file
    assert res.local_path is None


def test_download_interrupted_part_cleanup(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    mock_http = MagicMock()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.url = "http://example.com/broken.pdf"
    mock_response.headers = {
        "Content-Type": "application/pdf",
        "Content-Length": "10000",  # Expects 10,000 bytes
    }
    # Only yields 20 bytes (interrupted stream)
    mock_response.iter_content.return_value = [b"%PDF-1.4 initial"]
    mock_http.get_stream.return_value = mock_response

    downloader = BookDownloader(mock_http, cfg)
    book = BookRecord(
        stable_id="103",
        detail_url="https://www.freebookcentre.net/broken.html",
        title="Broken Book",
        download_url="https://www.freebookcentre.net/gotoweb.php?id=103",
        categories=["Math"],
    )

    res = downloader.download_book(book)

    assert res.status == BookStatus.FAILED
    assert "Incomplete download" in res.error
    # Verify no .part file was left behind
    part_files = list(tmp_path.rglob("*.part"))
    assert len(part_files) == 0


def test_already_existing_file_skipped(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    mock_http = MagicMock()

    book = BookRecord(
        stable_id="104",
        detail_url="https://www.freebookcentre.net/existing.html",
        title="Existing Book",
        download_url="https://www.freebookcentre.net/gotoweb.php?id=104",
        categories=["Physics"],
    )

    # Pre-create valid existing PDF file
    expected_path = tmp_path / "Physics" / "Existing Book [104].pdf"
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_data = b"%PDF-1.4 existing file content"
    expected_path.write_bytes(pdf_data)

    downloader = BookDownloader(mock_http, cfg)
    res = downloader.download_book(book)

    assert res.status == BookStatus.ALREADY_EXISTED
    assert res.local_path == str(expected_path)
    assert res.file_size == len(pdf_data)
    # Ensure network stream was never called
    mock_http.get_stream.assert_not_called()
