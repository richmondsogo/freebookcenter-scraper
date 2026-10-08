"""Download handling, streaming, magic-byte validation, and atomic filesystem storage."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from scraper.config import ScraperConfig
from scraper.http_client import HttpClient
from scraper.models import BookRecord, BookStatus, DownloadResult
from scraper.parser import determine_local_filepath


# Recognized binary file signatures
SIGNATURE_PDF = b"%PDF-"
SIGNATURE_ZIP = b"PK\x03\x04"
SIGNATURE_GZ = b"\x1f\x8b"
SIGNATURE_DJVU = b"AT&T"


def detect_file_type(first_bytes: bytes, content_type: str | None = None) -> tuple[str | None, bool]:
    """
    Detect file format based on magic bytes and Content-Type header.
    Returns:
        (detected_extension, is_html)
    """
    ct = (content_type or "").lower()

    # Check for HTML signatures
    stripped_start = first_bytes[:200].lstrip().lower()
    if (
        b"<!doctype html" in stripped_start
        or b"<html" in stripped_start
        or "text/html" in ct
    ):
        return None, True

    # Magic byte signatures
    if first_bytes.startswith(SIGNATURE_PDF) or b"%PDF-" in first_bytes[:1024]:
        return "pdf", False
    if first_bytes.startswith(SIGNATURE_ZIP):
        if "epub" in ct:
            return "epub", False
        return "zip", False
    if first_bytes.startswith(SIGNATURE_GZ):
        return "gz", False
    if first_bytes.startswith(SIGNATURE_DJVU):
        return "djvu", False

    # Content-type fallbacks for binary files
    if "application/pdf" in ct or "pdf" in ct:
        return "pdf", False
    if "application/zip" in ct:
        return "zip", False
    if "application/epub+zip" in ct:
        return "epub", False
    if "application/octet-stream" in ct:
        return "bin", False

    return None, False


class BookDownloader:
    """Manages the resolution, validation, streaming, and saving of books."""

    def __init__(self, http_client: HttpClient, config: ScraperConfig | None = None) -> None:
        self.http = http_client
        self.config = config or ScraperConfig()

    def download_book(self, book: BookRecord) -> DownloadResult:
        """
        Download and validate a book file, saving it into the configured directory tree.
        """
        if not book.download_url:
            return DownloadResult(
                status=BookStatus.NO_DOWNLOAD_LINK,
                error="No download URL exposed by book page",
            )

        # Check if already downloaded and valid
        target_path = determine_local_filepath(
            output_dir=self.config.output_dir,
            categories=book.categories,
            title=book.title,
            stable_id=book.stable_id,
            extension=book.file_type or "pdf",
        )

        if target_path.exists() and target_path.stat().st_size > 0:
            try:
                with open(target_path, "rb") as f:
                    header = f.read(1024)
                file_ext, is_html = detect_file_type(header)
                if not is_html and header.startswith(SIGNATURE_PDF):
                    # Valid existing file
                    size = target_path.stat().st_size
                    hasher = hashlib.sha256()
                    with open(target_path, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""):
                            hasher.update(chunk)
                    return DownloadResult(
                        status=BookStatus.ALREADY_EXISTED,
                        local_path=str(target_path),
                        file_type="pdf",
                        file_size=size,
                        sha256=hasher.hexdigest(),
                    )
            except Exception:
                # Corrupted or unreadable, re-download
                pass

        try:
            # Start streaming request
            resp = self.http.get_stream(
                url=book.download_url,
                referer=book.detail_url,
                timeout=self.config.download_timeout,
            )

            final_url = resp.url
            status_code = resp.status_code

            if status_code != 200:
                resp.close()
                return DownloadResult(
                    status=BookStatus.FAILED,
                    resolved_url=final_url,
                    error=f"HTTP {status_code} from resolved download URL",
                )

            content_type = resp.headers.get("Content-Type", "")
            content_length_header = resp.headers.get("Content-Length")
            expected_size = int(content_length_header) if content_length_header and content_length_header.isdigit() else None

            # Read first chunk to validate signature before saving
            stream_iter = iter(resp.iter_content(chunk_size=self.config.chunk_size))
            try:
                first_chunk = next(stream_iter)
            except StopIteration:
                resp.close()
                return DownloadResult(
                    status=BookStatus.FAILED,
                    resolved_url=final_url,
                    error="Empty response body received from server",
                )

            file_ext, is_html = detect_file_type(first_chunk, content_type)

            if is_html:
                resp.close()
                return DownloadResult(
                    status=BookStatus.VIEW_ONLY_HTML,
                    resolved_url=final_url,
                    error="Resolved resource is an online HTML page/viewer, not a downloadable file",
                )

            # Default extension to pdf if detected or binary
            final_ext = file_ext or "pdf"

            # Re-determine file path with verified extension
            final_path = determine_local_filepath(
                output_dir=self.config.output_dir,
                categories=book.categories,
                title=book.title,
                stable_id=book.stable_id,
                extension=final_ext,
            )
            part_path = final_path.with_name(f"{final_path.name}.part")

            # Ensure directory exists
            final_path.parent.mkdir(parents=True, exist_ok=True)

            # Clean any old .part file
            if part_path.exists():
                try:
                    part_path.unlink()
                except OSError:
                    pass

            # Stream chunks to .part file
            hasher = hashlib.sha256()
            bytes_written = 0

            with open(part_path, "wb") as f_out:
                f_out.write(first_chunk)
                hasher.update(first_chunk)
                bytes_written += len(first_chunk)

                for chunk in stream_iter:
                    if not chunk:
                        continue
                    f_out.write(chunk)
                    hasher.update(chunk)
                    bytes_written += len(chunk)

                    if bytes_written > self.config.max_file_size:
                        resp.close()
                        part_path.unlink(missing_ok=True)
                        return DownloadResult(
                            status=BookStatus.FAILED,
                            resolved_url=final_url,
                            error=f"File exceeded maximum size limit ({bytes_written} bytes)",
                        )

            resp.close()

            # Verify size integrity
            if bytes_written == 0:
                part_path.unlink(missing_ok=True)
                return DownloadResult(
                    status=BookStatus.FAILED,
                    resolved_url=final_url,
                    error="Zero bytes written to disk",
                )

            if expected_size and bytes_written < expected_size:
                part_path.unlink(missing_ok=True)
                return DownloadResult(
                    status=BookStatus.FAILED,
                    resolved_url=final_url,
                    error=f"Incomplete download: received {bytes_written} bytes, expected {expected_size}",
                )

            # Atomic rename from .part to final destination
            os.replace(part_path, final_path)

            return DownloadResult(
                status=BookStatus.DOWNLOADED,
                resolved_url=final_url,
                local_path=str(final_path),
                file_type=final_ext,
                file_size=bytes_written,
                sha256=hasher.hexdigest(),
            )

        except Exception as exc:
            # Handle unexpected network or I/O failure
            err_msg = f"{type(exc).__name__}: {str(exc)}"
            return DownloadResult(
                status=BookStatus.FAILED,
                error=err_msg,
            )
