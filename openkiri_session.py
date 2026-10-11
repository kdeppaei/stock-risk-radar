"""Conservative session/freshness labels from provider facts, not invented holidays."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openkiri_data import finite, iso_time

INTRADAY_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


def timestamp(value) -> float | None:
    stamp = iso_time(value)
    return datetime.fromisoformat(stamp).timestamp() if stamp else None


def market_status(meta: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    result = {"state": "unknown", "basis": "unconfirmed", "checked_at": iso_time(now),
              "valid_until": iso_time(now + 300), "timezone": meta.get("exchange_timezone"),
              "regular_start": None, "regular_end": None, "source_fetched_at": iso_time(meta.get("fetched_at"))}
    periods = meta.get("sessions") or {}
    if not isinstance(periods, dict):
        periods = {}
    regular = periods.get("regular") or {}
    if not isinstance(regular, dict):
        regular = {}
    start, end = finite(regular.get("start")), finite(regular.get("end"))
    if not start or not end or not 0 < end - start <= 86400:
        start = end = None
    else:
        result.update(regular_start=iso_time(start), regular_end=iso_time(end))

    state = str(meta.get("market_state") or "").upper()
    observed = timestamp(meta.get("fetched_at"))
    provider_states = {"REGULAR": "regular", "PRE": "pre", "PREPRE": "pre", "POST": "post",
                       "POSTPOST": "post", "CLOSED": "closed"}
    if state in provider_states and observed is not None and 0 <= now - observed <= 300:
        result.update(state=provider_states[state], basis="provider_state")
        result["valid_until"] = iso_time(min(observed + 300, now + 300))
        if state == "REGULAR" and start and not start <= now < end:
            result.update(state="unknown", basis="conflicting_source")
    else:
        for key in ("regular", "pre", "post"):
            window = periods.get(key) or {}
            if not isinstance(window, dict):
                continue
            left, right = finite(window.get("start")), finite(window.get("end"))
            if left and right and 0 < right - left <= 86400 and left <= now < right:
                result.update(state=key, basis="provider_session", valid_until=iso_time(min(now + 300, right)))
                break

    try:
        tz = ZoneInfo(str(result["timezone"]))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        offset = finite(regular.get("gmtoffset"))
        tz = timezone(timedelta(seconds=offset)) if offset is not None and abs(offset) <= 50400 else None
    if result["state"] == "unknown" and result["basis"] == "unconfirmed" and tz:
        local = datetime.fromtimestamp(now, tz)
        # Weekend classification is limited to supported conventional stock sessions.
        if result["timezone"] in {"Asia/Taipei", "America/New_York"} and local.weekday() >= 5:
            result.update(state="closed", basis="regular_weekend")
        elif start and datetime.fromtimestamp(start, tz).date() == local.date():
            result.update(state="closed", basis="outside_provider_session")
    if start:
        boundaries = [finite(window.get(key)) for window in periods.values() if isinstance(window, dict)
                      for key in ("start", "end")]
        next_boundary = min((boundary for boundary in boundaries if boundary and boundary > now), default=now + 300)
        result["valid_until"] = iso_time(min(timestamp(result["valid_until"]), next_boundary))
    return result


def observation_status(meta: dict, market: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    stamp = timestamp(meta.get("as_of"))
    interval = meta.get("interval")
    bar = meta.get("time_basis") == "bar_start"
    result = {"state": "missing_time", "as_of": iso_time(stamp), "age_seconds": None,
              "interval": interval, "time_basis": meta.get("time_basis"), "realtime_verified": False,
              "stale_after_seconds": max(900, 2 * INTRADAY_SECONDS.get(interval, 0))}
    if stamp is None:
        return result
    age = now - stamp
    result["age_seconds"] = round(max(0, age))
    if age < -120:
        result["state"] = "future_time"
        return result
    start, end = timestamp(market.get("regular_start")), timestamp(market.get("regular_end"))
    if market["state"] in {"pre", "post"} and meta.get("quote_session") == "regular":
        result["state"] = "regular_session_only"
    elif market["state"] == "closed" and start and end and 0 <= now - end <= 7 * 86400 and (
            (bar and interval == "1d" and start <= stamp < end)
            or (end - result["stale_after_seconds"] <= stamp <= end + 120)):
        result["state"] = "last_session"
    elif bar and interval not in INTRADAY_SECONDS:
        result["state"] = "historical_bar"
        result["stale_after_seconds"] = 28 * 86400 if interval == "1wk" else 7 * 86400
        if age > result["stale_after_seconds"]:
            result["state"] = "older_data"
    elif age > result["stale_after_seconds"]:
        result["state"] = "delayed" if market["state"] == "regular" else "older_data"
    else:
        result["state"] = "recent_observation"
    return result


def analysis_status(payload: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    sources = payload.get("provenance") or {}
    chart = sources.get("chart") or {}
    price = sources.get("price") or chart
    market = market_status(price, now)
    chart_quality = observation_status(chart, market, now)
    if payload.get("requested_interval") in INTRADAY_SECONDS and payload.get("interval") not in INTRADAY_SECONDS:
        chart_quality["state"] = "daily_fallback"
    return {"market": market, "price": observation_status(price, market, now), "chart": chart_quality}
