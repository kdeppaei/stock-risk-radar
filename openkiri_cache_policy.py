from __future__ import annotations

from typing import Any


def cache_prune(cache: dict[Any, Any], max_items: int) -> None:
    """Remove the oldest timestamped cache records until the size limit is met."""
    if len(cache) <= max_items:
        return
    stale = sorted(cache.items(), key=lambda item: item[1][0])[: max(1, len(cache) - max_items)]
    for key, _ in stale:
        cache.pop(key, None)


def freeze_params(params: Any) -> tuple[tuple[str, str], ...]:
    """Convert request parameters into a stable, hashable cache-key component."""
    if not params:
        return ()
    if isinstance(params, dict):
        return tuple(sorted((str(key), repr(value)) for key, value in params.items()))
    try:
        return tuple(sorted((str(key), repr(value)) for key, value in params))
    except Exception:
        return (("params", repr(params)),)


def http_ttl(url: Any, params: Any) -> int:
    """Return an upstream-specific response-cache TTL in seconds."""
    text = str(url).lower()
    frozen = dict(freeze_params(params))
    interval = str(frozen.get("interval", "")).strip("'\"").lower()
    if "query1.finance.yahoo.com/v8/finance/chart" in text:
        if interval in {"1m", "5m"}:
            return 75
        if interval in {"15m", "1h"}:
            return 180
        return 900
    if "query1.finance.yahoo.com/v7/finance/quote" in text:
        return 45
    if "google.com/finance/quote" in text:
        return 300
    if "feeds.finance.yahoo.com" in text or "news.google.com/rss" in text:
        return 1800
    if "quotesummary" in text or "getcrumb" in text or "fc.yahoo.com" in text:
        return 3600
    if "bea.gov" in text:
        return 21600
    return 300 if text.startswith("http") else 0


def history_ttl(period: str, interval: str) -> int:
    """Return the price-history cache TTL for a requested data resolution."""
    if interval in {"1m", "5m"}:
        return 75
    if interval in {"15m", "1h"}:
        return 180
    if period in {"1d", "5d"}:
        return 300
    return 900


def analyze_ttl(period: str, interval: str) -> int:
    """Return the completed-analysis cache TTL for a requested time window."""
    if period in {"1d", "5d"} and interval in {"5m", "15m", "1h"}:
        return 45
    if interval in {"5m", "15m", "1h"}:
        return 120
    return 600
