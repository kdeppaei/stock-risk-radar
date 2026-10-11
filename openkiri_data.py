"""Provider-independent rules for identity, timestamps, and valuation provenance."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def finite(value: Any, positive: bool = False) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    try:
        number = float(value)
        return number if math.isfinite(number) and (not positive or number > 0) else None
    except (ValueError, TypeError):
        return None


def iso_time(value: Any) -> str | None:
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value, timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def provenance(source: str, as_of: Any, fetched_at: Any, **extra: Any) -> dict[str, Any]:
    return {"source": source, "as_of": iso_time(as_of), "fetched_at": iso_time(fetched_at),
            "realtime_verified": False, **extra}


def empty_valuation(symbol: str, currency: str | None = None) -> dict[str, Any]:
    return {"symbol": symbol, "market_cap": None, "currency": currency, "trailing_pe": None,
            "forward_pe": None, "market_cap_change_pct": None, "market_cap_previous": None,
            "source": None, "as_of": None, "fetched_at": None, "status": "unavailable",
            "estimated": False, "fallback": False, "change_basis": None}


def valuation_from_snapshot(symbol: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    result = empty_valuation(symbol, snapshot.get("currency"))
    if str(snapshot.get("symbol", "")).upper() != symbol.upper():
        return result
    result.update({key: snapshot.get(key) for key in ("source", "fetched_at")})
    # Quote time does not establish the financial field's publication time.
    result["as_of"] = iso_time(snapshot.get("valuation_as_of"))
    result["quote_as_of"] = iso_time(snapshot.get("as_of"))
    result["market_cap"] = finite(snapshot.get("market_cap"), positive=True) if result["currency"] else None
    result["trailing_pe"] = finite(snapshot.get("trailing_pe"), positive=True)
    result["forward_pe"] = finite(snapshot.get("forward_pe"), positive=True)
    result["shares_outstanding"] = finite(snapshot.get("shares_outstanding"), positive=True)
    if any(result.get(key) is not None for key in ("market_cap", "trailing_pe", "forward_pe")):
        result["status"] = "reported"
    return result


def valuation_from_summary(symbol: str, summary: dict[str, Any], fetched_at: Any) -> dict[str, Any]:
    price = summary.get("price") or {}
    detail = summary.get("summaryDetail") or {}
    stats = summary.get("defaultKeyStatistics") or {}
    snapshot = {
        "symbol": price.get("symbol"), "currency": price.get("currency"),
        "market_cap": price.get("marketCap"),
        "trailing_pe": detail.get("trailingPE") or stats.get("trailingPE"),
        "forward_pe": detail.get("forwardPE") or stats.get("forwardPE"),
        "shares_outstanding": stats.get("sharesOutstanding"),
        "source": "Yahoo quoteSummary", "fetched_at": iso_time(fetched_at),
        "as_of": finite(price.get("regularMarketTime")),
    }
    return valuation_from_snapshot(symbol, snapshot)
