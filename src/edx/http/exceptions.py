"""Exceptions raised by the HTTP layer."""

from __future__ import annotations


class RobotsDisallowedError(RuntimeError):
    """Raised when ``robots.txt`` forbids access to the requested URL."""

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(f"robots.txt disallows access to {url}")


class ScrapeFailedError(RuntimeError):
    """Raised when scraping fails after all retries are exhausted."""
