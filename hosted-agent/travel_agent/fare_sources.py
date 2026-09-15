"""Trusted public sources for travel fare searches."""

from urllib.parse import urlparse


ALLOWED_FARE_DOMAINS = (
    "jreast.co.jp",
    "jr-central.co.jp",
    "jr-odekake.net",
    "jrkyushu.co.jp",
    "jrhokkaido.co.jp",
    "jr-shikoku.co.jp",
    "smart-ex.jp",
    "eki-net.com",
    "tokyometro.jp",
    "kotsu.metro.tokyo.jp",
    "ekitan.com",
    "transit.yahoo.co.jp",
    "jorudan.co.jp",
    "navitime.co.jp",
)


def is_allowed_fare_source(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower().removeprefix("www.")
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in ALLOWED_FARE_DOMAINS
    )
