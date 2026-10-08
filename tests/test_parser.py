"""Tests for URL canonicalization, HTML parsing, Schema.org metadata, and sanitization."""

from pathlib import Path

from scraper.parser import (
    canonicalize_url,
    clean_category_name,
    determine_local_filepath,
    is_book_detail_url,
    is_ignorable_url,
    is_internal_url,
    parse_book_detail_page,
    parse_listing_page,
    sanitize_filename,
)
from tests.fixtures import SAMPLE_BOOK_DETAIL_HTML, SAMPLE_CATEGORY_LISTING_HTML


def test_canonicalize_url():
    base = "https://www.freebookcentre.net/CompuScience/foo.html"

    # Relative resolution
    assert (
        canonicalize_url("../ComputerScience-Books-Download/bar.html", base)
        == "https://www.freebookcentre.net/ComputerScience-Books-Download/bar.html"
    )

    # Fragment removal
    assert (
        canonicalize_url("https://www.freebookcentre.net/test.html#section2")
        == "https://www.freebookcentre.net/test.html"
    )

    # Domain normalization (http -> https, lowercase)
    assert (
        canonicalize_url("http://freebookcentre.net/sample.html")
        == "https://www.freebookcentre.net/sample.html"
    )

    # Preserving ID query parameter for gotoweb.php
    assert (
        canonicalize_url("http://www.freebookcentre.net/gotoweb.php?id=12345&utm_source=test")
        == "https://www.freebookcentre.net/gotoweb.php?id=12345"
    )

    # Removing query parameters from normal pages
    assert (
        canonicalize_url("https://www.freebookcentre.net/index.html?ref=abc")
        == "https://www.freebookcentre.net/index.html"
    )


def test_is_internal_and_ignorable():
    assert is_internal_url("https://www.freebookcentre.net/test.html")
    assert is_internal_url("http://freebookcentre.net/test.html")
    assert not is_internal_url("https://google.com/test.html")

    assert is_ignorable_url("https://www.freebookcentre.net/AboutUs.html")
    assert is_ignorable_url("https://www.freebookcentre.net/Policy.html")
    assert is_ignorable_url("https://www.freebookcentre.net/image.png")
    assert not is_ignorable_url("https://www.freebookcentre.net/ComputerScience-Books-Download/test.html")


def test_is_book_detail_url():
    assert is_book_detail_url(
        "https://www.freebookcentre.net/ComputerScience-Books-Download/Intro-to-AI.html"
    )
    assert is_book_detail_url(
        "https://www.freebookcentre.net/database_books_download/SQL-Guide.html"
    )
    assert not is_book_detail_url(
        "https://www.freebookcentre.net/CompuScience/compscCategory.html"
    )
    assert not is_book_detail_url(
        "https://www.freebookcentre.net/CompuScience/Free-AI-Books-Download.html"
    )


def test_sanitize_filename():
    # Strips Windows illegal characters
    unsafe = 'Book: "Advanced AI" / ML? <2nd> Edition | *Special*'
    safe = sanitize_filename(unsafe)
    assert ":" not in safe
    assert '"' not in safe
    assert "/" not in safe
    assert "?" not in safe
    assert "<" not in safe
    assert ">" not in safe
    assert "|" not in safe
    assert "*" not in safe

    # Strips trailing dots and spaces
    assert sanitize_filename("My Book. . . ") == "My Book"

    # Handles reserved DOS names
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("NUL.txt") == "_NUL.txt"


def test_parse_listing_page():
    page_url = "https://www.freebookcentre.net/CompuScience/Free-Artificial-Intelligence-Books-Download.html"
    book_urls, sub_listings = parse_listing_page(SAMPLE_CATEGORY_LISTING_HTML, page_url)

    assert len(book_urls) == 2
    assert (
        "https://www.freebookcentre.net/ComputerScience-Books-Download/Introduction-to-Artificial-Intelligence-by-Marc-Toussaint.html"
        in book_urls
    )
    assert (
        "https://www.freebookcentre.net/ComputerScience-Books-Download/Digital-Notes-on-Artificial-Intelligence-by-Sri-Indu-College-of-Engineering-and-Technology.html"
        in book_urls
    )

    # Sub listings should include categories, but exclude AboutUs/Policy
    assert "https://www.freebookcentre.net/CompuScience/Free-Compiler-Design-Books-Download.html" in sub_listings
    assert not any("aboutus.html" in u.lower() for u in sub_listings)
    assert not any("policy.html" in u.lower() for u in sub_listings)


def test_parse_book_detail_page():
    page_url = "https://www.freebookcentre.net/ComputerScience-Books-Download/Introduction-to-Artificial-Intelligence-by-Marc-Toussaint.html"
    data = parse_book_detail_page(SAMPLE_BOOK_DETAIL_HTML, page_url)

    assert data["title"] == "Introduction to Artificial Intelligence by Marc Toussaint"
    assert data["authors"] == ["Marc Toussaint"]
    assert "comprehensive overview" in data["description"]
    assert data["pages"] == "248"
    assert data["stable_id"] == "10748"
    assert (
        data["download_url"]
        == "https://www.freebookcentre.net/ComputerScience-Books-Download/gotoweb.php?id=10748"
    )
    assert "Computer Science" in data["categories"]
    assert "Artificial Intelligence" in data["categories"]


def test_determine_local_filepath():
    out = Path("ebooks")
    path = determine_local_filepath(
        output_dir=out,
        categories=["Computer Science", "Artificial Intelligence"],
        title="Intro to AI",
        stable_id="10748",
        extension="pdf",
    )
    assert path == Path("ebooks/Computer Science/Artificial Intelligence/Intro to AI [10748].pdf")


def test_determine_local_filepath_long_title_and_slug():
    out = Path("ebooks")
    long_title = "A Queens Delight The Art of Preserving, Conserving and Candying. As also, A right Knowledge of makin"
    long_slug = "A-Queens-Delight-The-Art-of-Preserving,-Conserving-and-Candying.-As-also,-A-right-Knowledge-of-making-Perfumes,-and-Distilling-the-most-Excellent-Waters."
    path = determine_local_filepath(
        output_dir=out,
        categories=["Cooking", "Cooking, Food and Drink eBooks"],
        title=long_title,
        stable_id=long_slug,
        extension="pdf",
    )
    # Filename must be <= 110 characters, not end with a dot before extension, and have safe length
    assert len(path.name) <= 115
    assert not path.stem.endswith(".")
    # Must be capable of unlinking/referencing without Windows WinError 123
    part_path = path.with_name(f"{path.name}.part")
    part_path.unlink(missing_ok=True)

