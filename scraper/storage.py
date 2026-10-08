"""Persistent storage and state management using SQLite with JSON/CSV catalog exports."""

from __future__ import annotations

import csv
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scraper.config import ScraperConfig
from scraper.models import BookRecord, BookStatus, CrawlStats, DownloadResult


class Storage:
    """Thread-safe SQLite storage for crawler state, metadata, and resume support."""

    def __init__(self, config: ScraperConfig | None = None) -> None:
        self.config = config or ScraperConfig()
        self.db_path = self.config.db_path
        self._lock = threading.Lock()

        # Ensure output directory exists
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS crawl_urls (
                        url TEXT PRIMARY KEY,
                        url_type TEXT NOT NULL,
                        visited INTEGER DEFAULT 0,
                        discovered_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS books (
                        detail_url TEXT PRIMARY KEY,
                        stable_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        authors TEXT,
                        download_url TEXT,
                        resolved_url TEXT,
                        categories TEXT,
                        description TEXT,
                        pages TEXT,
                        file_type TEXT,
                        file_size INTEGER,
                        sha256 TEXT,
                        local_path TEXT,
                        status TEXT NOT NULL,
                        error TEXT,
                        discovered_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_books_status ON books(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_books_stable_id ON books(stable_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_urls_visited ON crawl_urls(url_type, visited)")

    # -------------------------------------------------------------
    # URL Management
    # -------------------------------------------------------------

    def add_crawl_url(self, url: str, url_type: str) -> bool:
        """Add a URL to crawl_urls if not already present. Returns True if inserted."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO crawl_urls (url, url_type, visited, discovered_at) VALUES (?, ?, 0, ?)",
                        (url, url_type, now),
                    )
                    return conn.total_changes > 0
                except sqlite3.Error:
                    return False

    def mark_url_visited(self, url: str) -> None:
        """Mark a URL as visited."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("UPDATE crawl_urls SET visited = 1 WHERE url = ?", (url,))

    def get_unvisited_listings(self, limit: int | None = None) -> list[str]:
        """Fetch unvisited listing/category URLs."""
        query = "SELECT url FROM crawl_urls WHERE url_type = 'listing' AND visited = 0"
        if limit:
            query += f" LIMIT {limit}"
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute(query).fetchall()
                return [row["url"] for row in rows]

    # -------------------------------------------------------------
    # Book Record Management
    # -------------------------------------------------------------

    def save_or_merge_book(self, book: BookRecord) -> None:
        """
        Insert or merge a book record. If the book already exists,
        new categories are merged without overwriting successful download states.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                existing = conn.execute(
                    "SELECT * FROM books WHERE detail_url = ?",
                    (book.detail_url,),
                ).fetchone()

                if existing:
                    # Merge categories
                    try:
                        existing_cats = json.loads(existing["categories"] or "[]")
                    except Exception:
                        existing_cats = []

                    combined_cats = list(dict.fromkeys(existing_cats + book.categories))

                    # Update categories and metadata if newer
                    conn.execute(
                        """
                        UPDATE books SET
                            categories = ?,
                            title = COALESCE(NULLIF(?, 'Unknown Title'), title),
                            authors = CASE WHEN authors IS NULL OR authors = '[]' THEN ? ELSE authors END,
                            download_url = COALESCE(?, download_url),
                            description = COALESCE(?, description),
                            pages = COALESCE(?, pages),
                            updated_at = ?
                        WHERE detail_url = ?
                        """,
                        (
                            json.dumps(combined_cats),
                            book.title,
                            json.dumps(book.authors),
                            book.download_url,
                            book.description,
                            book.pages,
                            now,
                            book.detail_url,
                        ),
                    )
                else:
                    # New book insertion
                    conn.execute(
                        """
                        INSERT INTO books (
                            detail_url, stable_id, title, authors, download_url,
                            resolved_url, categories, description, pages, file_type,
                            file_size, sha256, local_path, status, error,
                            discovered_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            book.detail_url,
                            book.stable_id,
                            book.title,
                            json.dumps(book.authors),
                            book.download_url,
                            book.resolved_url,
                            json.dumps(book.categories),
                            book.description,
                            book.pages,
                            book.file_type,
                            book.file_size,
                            book.sha256,
                            book.local_path,
                            book.status,
                            book.error,
                            book.discovered_at or now,
                            now,
                        ),
                    )

    def update_book_download(self, detail_url: str, result: DownloadResult) -> None:
        """Update a book record with download results."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE books SET
                        status = ?,
                        resolved_url = COALESCE(?, resolved_url),
                        local_path = COALESCE(?, local_path),
                        file_type = COALESCE(?, file_type),
                        file_size = COALESCE(?, file_size),
                        sha256 = COALESCE(?, sha256),
                        error = ?,
                        updated_at = ?
                    WHERE detail_url = ?
                    """,
                    (
                        result.status,
                        result.resolved_url,
                        result.local_path,
                        result.file_type,
                        result.file_size,
                        result.sha256,
                        result.error,
                        now,
                        detail_url,
                    ),
                )

    def get_books_to_download(self, retry_failed: bool = False, limit: int | None = None) -> list[BookRecord]:
        """Fetch books that need download processing."""
        statuses = [BookStatus.DISCOVERED, BookStatus.PARSED]
        if retry_failed:
            statuses.append(BookStatus.FAILED)

        placeholders = ",".join("?" for _ in statuses)
        query = f"SELECT * FROM books WHERE status IN ({placeholders})"
        if limit:
            query += f" LIMIT {limit}"

        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute(query, statuses).fetchall()
                return [BookRecord.from_dict(dict(r)) for r in rows]

    def get_all_books(self) -> list[BookRecord]:
        """Retrieve all books in the database."""
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute("SELECT * FROM books ORDER BY title ASC").fetchall()
                return [BookRecord.from_dict(dict(r)) for r in rows]

    # -------------------------------------------------------------
    # Statistics and Reporting
    # -------------------------------------------------------------

    def get_stats(self) -> CrawlStats:
        """Calculate overall crawl statistics."""
        with self._lock:
            with self._get_connection() as conn:
                disc_listings = conn.execute(
                    "SELECT COUNT(*) FROM crawl_urls WHERE url_type = 'listing'"
                ).fetchone()[0]
                vis_listings = conn.execute(
                    "SELECT COUNT(*) FROM crawl_urls WHERE url_type = 'listing' AND visited = 1"
                ).fetchone()[0]

                total_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
                status_counts = dict(
                    conn.execute("SELECT status, COUNT(*) FROM books GROUP BY status").fetchall()
                )

                # Categories count
                all_cats = set()
                cat_rows = conn.execute("SELECT categories FROM books").fetchall()
                for r in cat_rows:
                    try:
                        cats = json.loads(r[0] or "[]")
                        all_cats.update(cats)
                    except Exception:
                        pass

        downloaded = status_counts.get(BookStatus.DOWNLOADED, 0)
        already_existed = status_counts.get(BookStatus.ALREADY_EXISTED, 0)
        view_only = status_counts.get(BookStatus.VIEW_ONLY_HTML, 0)
        no_dl = status_counts.get(BookStatus.NO_DOWNLOAD_LINK, 0)
        failed = status_counts.get(BookStatus.FAILED, 0)

        downloadable = downloaded + already_existed

        return CrawlStats(
            discovered_urls=disc_listings,
            listing_pages_visited=vis_listings,
            book_pages_found=total_books,
            unique_books=total_books,
            downloadable_books=downloadable,
            downloaded=downloaded,
            already_existed=already_existed,
            view_only_html=view_only,
            no_download_found=no_dl,
            failed=failed,
            categories_processed=len(all_cats),
        )

    # -------------------------------------------------------------
    # Incremental Catalog Exports
    # -------------------------------------------------------------

    def export_catalog_json(self, filepath: Path | None = None) -> Path:
        """Export all books to catalog.json."""
        target = filepath or self.config.catalog_json_path
        books = self.get_all_books()
        data = [b.to_dict() for b in books]
        temp_target = target.with_suffix(".json.tmp")
        with open(temp_target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        temp_target.replace(target)
        return target

    def export_catalog_csv(self, filepath: Path | None = None) -> Path:
        """Export all books to catalog.csv."""
        target = filepath or self.config.catalog_csv_path
        books = self.get_all_books()
        temp_target = target.with_suffix(".csv.tmp")
        with open(temp_target, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "stable_id",
                    "title",
                    "authors",
                    "detail_url",
                    "download_url",
                    "resolved_url",
                    "categories",
                    "pages",
                    "file_type",
                    "file_size",
                    "sha256",
                    "local_path",
                    "status",
                    "error",
                ]
            )
            for b in books:
                writer.writerow(
                    [
                        b.stable_id,
                        b.title,
                        ", ".join(b.authors),
                        b.detail_url,
                        b.download_url or "",
                        b.resolved_url or "",
                        " > ".join(b.categories),
                        b.pages or "",
                        b.file_type or "",
                        b.file_size or "",
                        b.sha256 or "",
                        b.local_path or "",
                        b.status,
                        b.error or "",
                    ]
                )
        temp_target.replace(target)
        return target

    def export_failed_json(self, filepath: Path | None = None) -> Path:
        """Export failed and unresolved books to failed.json."""
        target = filepath or self.config.failed_json_path
        books = self.get_all_books()
        failed_books = [
            b.to_dict()
            for b in books
            if b.status in (BookStatus.FAILED, BookStatus.NO_DOWNLOAD_LINK)
        ]
        temp_target = target.with_suffix(".json.tmp")
        with open(temp_target, "w", encoding="utf-8") as f:
            json.dump(failed_books, f, indent=2, ensure_ascii=False)
        temp_target.replace(target)
        return target
