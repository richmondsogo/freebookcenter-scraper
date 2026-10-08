"""Configuration settings for FreeBookCentre scraper."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ScraperConfig:
    # Target Site URLs
    base_url: str = "https://www.freebookcentre.net"
    sitemap_url: str = "https://www.freebookcentre.net/Sitemap.html"

    # Filesystem paths
    output_dir: Path = Path("ebooks")
    db_filename: str = "crawler_state.db"
    catalog_json_filename: str = "catalog.json"
    catalog_csv_filename: str = "catalog.csv"
    failed_json_filename: str = "failed.json"

    # Worker concurrency
    max_workers: int = 4

    # Request timeouts & delays
    request_timeout: float = 30.0
    download_timeout: float = 60.0
    min_delay: float = 0.25
    max_delay: float = 0.75

    # Retries & Backoff
    max_retries: int = 3
    backoff_factor: float = 1.0

    # Download streaming
    chunk_size: int = 64 * 1024  # 64 KB
    max_file_size: int = 500 * 1024 * 1024  # 500 MB safeguard

    # Run limits
    limit_books: int | None = None
    smoke_test: bool = False
    retry_failed: bool = False
    discovery_only: bool = False
    download_only: bool = False
    verbose: bool = False

    # HTTP User-Agent
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    @property
    def db_path(self) -> Path:
        return self.output_dir / self.db_filename

    @property
    def catalog_json_path(self) -> Path:
        return self.output_dir / self.catalog_json_filename

    @property
    def catalog_csv_path(self) -> Path:
        return self.output_dir / self.catalog_csv_filename

    @property
    def failed_json_path(self) -> Path:
        return self.output_dir / self.failed_json_filename
