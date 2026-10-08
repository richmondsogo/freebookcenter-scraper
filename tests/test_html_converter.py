"""Unit tests for html_converter module."""

from pathlib import Path
from unittest.mock import MagicMock

from scraper.config import ScraperConfig
from scraper.html_converter import HtmlToPdfConverter, find_embedded_pdf_url
from scraper.models import BookRecord, BookStatus
from scraper.storage import Storage


def test_find_embedded_pdf_url():
    html_with_pdf = """
    <html>
        <body>
            <a href="/download/sample/sample.pdf">Download Full PDF Book</a>
            <a href="https://archive.org/download/item123/item123.pdf">Archive Download</a>
        </body>
    </html>
    """
    found = find_embedded_pdf_url(html_with_pdf, "https://archive.org/details/item123")
    assert found is not None
    assert found.endswith(".pdf")

    html_without_pdf = "<html><body><p>No links here</p></body></html>"
    assert find_embedded_pdf_url(html_without_pdf, "https://example.com") is None


def test_converter_skips_existing_valid_pdf(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    storage = Storage(cfg)

    book = BookRecord(
        stable_id="901",
        detail_url="https://www.freebookcentre.net/webbook.html",
        title="Web Book",
        status=BookStatus.VIEW_ONLY_HTML,
        categories=["Computer Science"],
        resolved_url="https://example.com/webbook",
    )
    storage.save_or_merge_book(book)

    # Pre-create valid target PDF
    expected_path = tmp_path / "Computer Science" / "Web Book [901].pdf"
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_bytes(b"%PDF-1.4 sample content with sufficient size" + b"0" * 1200)

    converter = HtmlToPdfConverter(storage, cfg)
    mock_page = MagicMock()

    res = converter.process_book(mock_page, book)
    assert res.status == BookStatus.DOWNLOADED
    assert res.local_path == str(expected_path)
    # Ensure Playwright goto was skipped
    mock_page.goto.assert_not_called()


def test_converter_handles_failure_and_cleans_part(tmp_path: Path):
    cfg = ScraperConfig(output_dir=tmp_path)
    storage = Storage(cfg)

    book = BookRecord(
        stable_id="902",
        detail_url="https://www.freebookcentre.net/deadbook.html",
        title="Dead Book",
        status=BookStatus.VIEW_ONLY_HTML,
        categories=["Physics"],
        resolved_url="https://deadlink.example.com",
    )
    storage.save_or_merge_book(book)

    converter = HtmlToPdfConverter(storage, cfg)
    mock_page = MagicMock()
    mock_page.goto.side_effect = RuntimeError("Connection timed out")

    res = converter.process_book(mock_page, book)
    assert res.status == BookStatus.FAILED
    assert "Connection timed out" in res.error

    part_file = tmp_path / "Physics" / "Dead Book [902].pdf.part"
    assert not part_file.exists()

