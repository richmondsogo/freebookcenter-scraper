"""HTTP client and session management with connection pooling, retries, and rate limiting."""

from __future__ import annotations

import random
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scraper.config import ScraperConfig

_thread_local = threading.local()


class HttpClient:
    """Thread-safe HTTP client for polite, resilient crawling and downloading."""

    def __init__(self, config: ScraperConfig | None = None) -> None:
        self.config = config or ScraperConfig()
        self._rate_limit_lock = threading.Lock()
        self._cooldown_until = 0.0

    def _create_session(self) -> requests.Session:
        session = requests.Session()

        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=15,
            pool_maxsize=15,
        )

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.headers.update(
            {
                "User-Agent": self.config.user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "application/pdf,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Connection": "keep-alive",
            }
        )
        return session

    def get_session(self) -> requests.Session:
        """Returns a thread-local session."""
        session = getattr(_thread_local, "session", None)
        if session is None:
            session = self._create_session()
            _thread_local.session = session
        return session

    def _polite_delay(self) -> None:
        """Sleep a small random duration to be polite, respecting cooldowns."""
        now = time.time()
        with self._rate_limit_lock:
            if now < self._cooldown_until:
                wait_time = self._cooldown_until - now
                time.sleep(wait_time)

        if self.config.min_delay > 0:
            delay = random.uniform(self.config.min_delay, self.config.max_delay)
            time.sleep(delay)

    def _handle_response_rate_limit(self, response: requests.Response) -> None:
        """Handle 429 Too Many Requests by establishing cooldown."""
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                cooldown = float(retry_after) if retry_after else 10.0
            except ValueError:
                cooldown = 10.0
            with self._rate_limit_lock:
                self._cooldown_until = max(self._cooldown_until, time.time() + cooldown)

    def fetch_html(
        self,
        url: str,
        referer: str | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, dict[str, str]]:
        """Fetch HTML text from url safely."""
        self._polite_delay()
        session = self.get_session()
        headers = {}
        if referer:
            headers["Referer"] = referer

        req_timeout = timeout or self.config.request_timeout
        try:
            resp = session.get(url, headers=headers, timeout=req_timeout, allow_redirects=True)
        except requests.exceptions.SSLError:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = session.get(url, headers=headers, timeout=req_timeout, allow_redirects=True, verify=False)

        self._handle_response_rate_limit(resp)
        return resp.status_code, resp.text, dict(resp.headers)

    def get_stream(
        self,
        url: str,
        referer: str | None = None,
        timeout: float | None = None,
    ) -> requests.Response:
        """Initiate a streaming GET request with redirects followed."""
        self._polite_delay()
        session = self.get_session()
        headers = {}
        if referer:
            headers["Referer"] = referer

        req_timeout = timeout or self.config.download_timeout
        try:
            resp = session.get(
                url,
                headers=headers,
                stream=True,
                timeout=req_timeout,
                allow_redirects=True,
            )
        except requests.exceptions.SSLError:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = session.get(
                url,
                headers=headers,
                stream=True,
                timeout=req_timeout,
                allow_redirects=True,
                verify=False,
            )

        self._handle_response_rate_limit(resp)
        return resp
