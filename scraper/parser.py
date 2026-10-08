"""HTML parsing, link extraction, Schema.org metadata, and filesystem sanitization."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from scraper.config import ScraperConfig

# Windows forbidden characters in filenames
WIN_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WIN_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

IGNORED_PATHS = {
    "/aboutus.html",
    "/contactus.html",
    "/link_us.html",
    "/policy.html",
    "/terms.html",
    "/postqueries.html",
    "/gdform_2.php",
    "/.htaccess",
}


def canonicalize_url(url: str, base_url: str = "https://www.freebookcentre.net") -> str:
    """Canonicalize a URL to avoid duplicate crawl work."""
    if not url:
        return ""

    # Resolve relative URL against base
    full_url = urljoin(base_url, url.strip())
    defrag_url, _ = urldefrag(full_url)
    parsed = urlparse(defrag_url)

    netloc = parsed.netloc.lower()
    if netloc == "freebookcentre.net":
        netloc = "www.freebookcentre.net"

    path = parsed.path
    # Collapse multiple consecutive slashes
    path = re.sub(r"/{2,}", "/", path)

    # Standardize scheme
    scheme = "https" if netloc == "www.freebookcentre.net" else parsed.scheme.lower()

    # Handle query parameters: preserve 'id' for gotoweb.php, drop trackers
    query = parsed.query
    if "gotoweb.php" in path:
        q_dict = dict(parse_qsl(query))
        preserved = {k: v for k, v in q_dict.items() if k.lower() in ("id", "book_id")}
        query = urlencode(preserved)
    else:
        query = ""

    if query:
        return f"{scheme}://{netloc}{path}?{query}"
    return f"{scheme}://{netloc}{path}"


def is_internal_url(url: str) -> bool:
    """Check if the URL belongs to FreeBookCentre."""
    netloc = urlparse(url).netloc.lower()
    return netloc in ("www.freebookcentre.net", "freebookcentre.net")


def is_ignorable_url(url: str) -> bool:
    """Filter out non-catalog pages like privacy policy, terms, contact, etc."""
    path = urlparse(url).path.lower()
    if path in IGNORED_PATHS or any(p in path for p in ["/gdform", "/.htaccess"]):
        return True
    if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".ico")):
        return True
    return False


def is_book_detail_url(url: str) -> bool:
    """Determine if a URL matches the pattern of a book detail page."""
    path = urlparse(url).path
    # Book detail pages match: /<Section>[-_][bB]ooks[-_][dD]ownload/<Book-Slug>.html
    return bool(re.search(r"/[^/]+[_-]books[_-]download/[^/]+\.html$", path, re.IGNORECASE))


def parse_listing_page(html: str, page_url: str) -> tuple[list[str], list[str]]:
    """
    Parse a category, topic, or sitemap listing page.
    Returns:
        (book_detail_urls, subcategory_listing_urls)
    """
    soup = BeautifulSoup(html, "html.parser")
    book_urls: list[str] = []
    listing_urls: list[str] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue

        full_url = canonicalize_url(href, page_url)
        if not is_internal_url(full_url) or is_ignorable_url(full_url):
            continue

        if "gotoweb.php" in full_url:
            continue

        if is_book_detail_url(full_url):
            if full_url not in book_urls:
                book_urls.append(full_url)
        else:
            # It's an internal catalog/listing/category page
            path = urlparse(full_url).path.lower()
            if path.endswith((".html", ".htm", "/")) or not "." in path.split("/")[-1]:
                if full_url not in listing_urls:
                    listing_urls.append(full_url)

    return book_urls, listing_urls


def clean_category_name(raw_name: str) -> str:
    """Normalize a category name by removing trailing 'Books' or 'Book' and trimming."""
    name = raw_name.strip()
    name = re.sub(r"\s+books?$", "", name, flags=re.IGNORECASE).strip()
    return name or "Uncategorized"


def parse_book_detail_page(html: str, page_url: str) -> dict[str, Any]:
    """
    Parse an individual book detail page to extract Schema.org metadata,
    breadcrumbs, and download links.
    """
    soup = BeautifulSoup(html, "html.parser")

    # 1. Title
    title = None
    book_div = soup.find("div", itemscope=True)
    if book_div:
        name_elem = book_div.find(itemprop="name")
        if name_elem:
            title = name_elem.get_text(strip=True)

    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    if not title:
        title_tag = soup.find("title")
        if title_tag:
            raw_title = title_tag.get_text(strip=True)
            title = raw_title.split("|")[0].strip()

    title = title or "Untitled Book"

    # 2. Authors
    authors: list[str] = []
    if book_div:
        author_elem = book_div.find(itemprop="author")
        if author_elem:
            raw_author = author_elem.get_text(strip=True)
            if raw_author:
                # Could be comma or semicolon separated
                authors = [a.strip() for a in re.split(r"[,;]|\band\b", raw_author) if a.strip()]

    if not authors:
        # Fallback to search for 'Author(s):' in text
        for p in soup.find_all(["p", "div", "span"]):
            text = p.get_text(" ", strip=True)
            if "Author(s):" in text:
                match = re.search(r"Author\(s\):\s*([^<>\n]+)", text)
                if match:
                    raw_author = match.group(1).strip()
                    authors = [a.strip() for a in re.split(r"[,;]|\band\b", raw_author) if a.strip()]
                    break

    # 3. Description
    description = None
    if book_div:
        desc_elem = book_div.find(itemprop="description")
        if desc_elem:
            description = desc_elem.get_text(" ", strip=True)

    # 4. Pages
    pages = None
    if book_div:
        pages_elem = book_div.find(itemprop="numberOfPages")
        if pages_elem:
            pages = pages_elem.get_text(strip=True)

    if not pages:
        page_span = soup.find(class_=lambda c: c and "pages-tab" in c)
        if page_span:
            pages = page_span.get_text(strip=True)

    if pages and pages.upper() == "NA":
        pages = None

    # 5. Breadcrumb Categories
    categories: list[str] = []
    brd = soup.find("div", class_=lambda c: c and "brd-txt" in c)
    if brd:
        for item in brd.find_all(["span", "a"]):
            text = item.get_text(strip=True).strip("/")
            if text and not any(k in text.lower() for k in ["home", "download", "welcome"]):
                cleaned = clean_category_name(text)
                if cleaned not in categories:
                    categories.append(cleaned)

    # 6. Download Link & Stable ID
    download_url = None
    stable_id = None

    # Search for gotoweb.php link
    dl_tag = soup.find("a", href=lambda h: h and "gotoweb.php" in h)
    if dl_tag:
        raw_href = dl_tag["href"]
        download_url = canonicalize_url(raw_href, page_url)
        id_match = re.search(r"id=(\d+)", raw_href)
        if id_match:
            stable_id = id_match.group(1)

    if not stable_id:
        # Derive stable_id from the URL slug
        path = urlparse(page_url).path
        slug = Path(path).stem
        stable_id = slug or "book"

    return {
        "title": title,
        "authors": authors,
        "description": description,
        "pages": pages,
        "categories": categories,
        "download_url": download_url,
        "stable_id": stable_id,
        "detail_url": canonicalize_url(page_url),
    }


def sanitize_filename(name: str, max_length: int = 120) -> str:
    """Sanitize a filename for safe storage on Windows and Unix systems."""
    if not name:
        return "unnamed_file"

    # Replace forbidden characters with spaces or underscores
    clean = WIN_FORBIDDEN_CHARS.sub(" ", name)
    # Collapse multiple whitespace
    clean = re.sub(r"\s+", " ", clean).strip()

    # Strip trailing periods and spaces (illegal on Windows)
    clean = clean.rstrip(". ")

    # Check Windows reserved filenames
    base_stem = clean.split(".")[0].upper()
    if base_stem in WIN_RESERVED_NAMES:
        clean = f"_{clean}"

    # Truncate to max_length while preserving clean boundary
    if len(clean) > max_length:
        clean = clean[:max_length].rstrip(". ")

    return clean or "unnamed_file"


def determine_local_filepath(
    output_dir: Path,
    categories: list[str],
    title: str,
    stable_id: str,
    extension: str = "pdf",
) -> Path:
    """
    Organize downloaded books into a hierarchical directory tree based on category.
    Example:
        output_dir / "Computer Science" / "Artificial Intelligence" / "Title [10748].pdf"
    """
    clean_cats = [sanitize_filename(cat, max_length=45) for cat in categories if cat.strip()]
    if not clean_cats:
        clean_cats = ["Uncategorized"]

    # Limit hierarchy depth to 2 folders (Category / Subcategory)
    cat_path = Path(*clean_cats[:2])

    safe_title = sanitize_filename(title, max_length=70)
    safe_id = sanitize_filename(stable_id, max_length=30)
    safe_ext = extension.lstrip(".")

    # Ensure base filename fits safely within Windows limits
    base_name = f"{safe_title} [{safe_id}]" if safe_id else safe_title
    if len(base_name) > 100:
        base_name = base_name[:100].rstrip(". ")

    filename = f"{base_name}.{safe_ext}"

    return output_dir / cat_path / filename

