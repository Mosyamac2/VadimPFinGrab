"""HTTP layer: rate-limited polite scraping client + robots.txt cache."""

from edx.http.client import DownloadResult, EDisclosureClient, build_user_agent
from edx.http.exceptions import RobotsDisallowedError, ScrapeFailedError
from edx.http.robots import RobotsCache

__all__ = [
    "DownloadResult",
    "EDisclosureClient",
    "RobotsCache",
    "RobotsDisallowedError",
    "ScrapeFailedError",
    "build_user_agent",
]
