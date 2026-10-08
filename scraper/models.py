"""Data models for FreeBookCentre crawler and downloader."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


class BookStatus:
    DISCOVERED = "discovered"
    PARSED = "parsed"
    DOWNLOADED = "downloaded"
    ALREADY_EXISTED = "already_existed"
    VIEW_ONLY_HTML = "view_only_html"
    NO_DOWNLOAD_LINK = "no_download_link"
    FAILED = "failed"


@dataclass
class BookRecord:
    stable_id: str
    detail_url: str
    title: str = "Unknown Title"
    authors: list[str] = field(default_factory=list)
    download_url: str | None = None
    resolved_url: str | None = None
    categories: list[str] = field(default_factory=list)
    description: str | None = None
    pages: str | None = None
    file_type: str | None = None
    file_size: int | None = None
    sha256: str | None = None
    local_path: str | None = None
    status: str = BookStatus.DISCOVERED
    error: str | None = None
    discovered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BookRecord:
        # Handle list deserialization if stored as string/json
        authors = data.get("authors")
        if isinstance(authors, str):
            try:
                authors = json.loads(authors)
            except Exception:
                authors = [a.strip() for a in authors.split(",") if a.strip()]
        elif authors is None:
            authors = []

        categories = data.get("categories")
        if isinstance(categories, str):
            try:
                categories = json.loads(categories)
            except Exception:
                categories = [c.strip() for c in categories.split(",") if c.strip()]
        elif categories is None:
            categories = []

        return cls(
            stable_id=data.get("stable_id", ""),
            detail_url=data.get("detail_url", ""),
            title=data.get("title") or "Unknown Title",
            authors=authors,
            download_url=data.get("download_url"),
            resolved_url=data.get("resolved_url"),
            categories=categories,
            description=data.get("description"),
            pages=data.get("pages"),
            file_type=data.get("file_type"),
            file_size=data.get("file_size"),
            sha256=data.get("sha256"),
            local_path=data.get("local_path"),
            status=data.get("status", BookStatus.DISCOVERED),
            error=data.get("error"),
            discovered_at=data.get("discovered_at") or datetime.now(timezone.utc).isoformat(),
            updated_at=data.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        )


@dataclass
class DownloadResult:
    status: str
    resolved_url: str | None = None
    local_path: str | None = None
    file_type: str | None = None
    file_size: int | None = None
    sha256: str | None = None
    error: str | None = None


@dataclass
class CrawlStats:
    discovered_urls: int = 0
    listing_pages_visited: int = 0
    book_pages_found: int = 0
    unique_books: int = 0
    downloadable_books: int = 0
    downloaded: int = 0
    already_existed: int = 0
    skipped: int = 0
    failed: int = 0
    no_download_found: int = 0
    view_only_html: int = 0
    categories_processed: int = 0

    def print_summary(self) -> None:
        print("\n" + "=" * 48)
        print("                 CRAWL SUMMARY                  ")
        print("=" * 48)
        print(f"Discovered listing URLs:   {self.discovered_urls:,}")
        print(f"Listing pages visited:     {self.listing_pages_visited:,}")
        print(f"Book detail pages found:   {self.book_pages_found:,}")
        print(f"Unique books:              {self.unique_books:,}")
        print(f"Downloadable books:        {self.downloadable_books:,}")
        print(f"Downloaded new:            {self.downloaded:,}")
        print(f"Already existed:           {self.already_existed:,}")
        print(f"View-only / HTML web:      {self.view_only_html:,}")
        print(f"No download link:          {self.no_download_found:,}")
        print(f"Failed / Unresolved:       {self.failed:,}")
        print(f"Categories processed:      {self.categories_processed:,}")
        print("=" * 48 + "\n")
