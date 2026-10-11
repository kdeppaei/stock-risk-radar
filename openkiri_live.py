from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from typing import Any

import requests
from fastapi import HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse

import app as base
from openkiri_cache_policy import analyze_ttl, cache_prune, freeze_params, history_ttl, http_ttl
from openkiri_data import empty_valuation, finite, iso_time, provenance, valuation_from_snapshot
from openkiri_metrics import METRICS, MetricsMiddleware, provider_group
from openkiri_valuation import public_valuation

app = base.app
app.add_middleware(GZipMiddleware, minimum_size=512, compresslevel=6)
app.add_middleware(MetricsMiddleware)
BASE_DIR = Path(__file__).resolve().parent
ALERT_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
ANALYZE_CACHE: dict[tuple[str, str, str, bool], tuple[float, dict[str, Any]]] = {}
HTTP_GET_CACHE: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[float, int, bytes, dict[str, str], str, str | None]] = {}
PRICE_HISTORY_CACHE: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
NEWS_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
MACRO_CACHE: tuple[float, dict[str, Any]] | None = None
MARKET_PULSE_SAVER_CACHE: tuple[float, dict[str, Any]] | None = None
RECOMMENDATIONS_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = Lock()
_HTTP_LOCKS = [RLock() for _ in range(32)]
_QUOTE_LOCKS = [RLock() for _ in range(32)]
_VALUATION_LOCKS = [RLock() for _ in range(32)]
VALUATION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_ORIGINAL_REQUESTS_GET = requests.get
_ORIGINAL_SESSION_SEND = requests.Session.send
_ORIGINAL_FETCH_PRICE_HISTORY = base.fetch_price_history
_ORIGINAL_FETCH_NEWS = base.fetch_news
_ORIGINAL_FETCH_MACRO_SNAPSHOT = base.fetch_macro_snapshot


def cached_response(record: tuple[float, int, bytes, dict[str, str], str, str | None]) -> requests.Response:
    _, status, content, headers, url, encoding = record
    response = requests.Response()
    response.status_code = status
    response._content = content
    response._content_consumed = True
    response.headers.update(headers)
    response.url = url
    response.encoding = encoding
    response.openkiri_fetched_at = record[0]
    return response


def bandwidth_cached_get(url: Any, *args: Any, **kwargs: Any) -> requests.Response:
    if kwargs.get("stream"):
        return _ORIGINAL_REQUESTS_GET(url, *args, **kwargs)
    params = kwargs.get("params")
    ttl = http_ttl(url, params)
    key = (str(url), freeze_params(params))
    # Coalesce simultaneous identical public reads, including bounded negative caching.
    with _HTTP_LOCKS[hash(key) % len(_HTTP_LOCKS)]:
        now = time.time()
        if ttl:
            with _CACHE_LOCK:
                cached = HTTP_GET_CACHE.get(key)
                lifetime = min(ttl, 60) if cached and cached[1] != 200 else ttl
                if cached and now - cached[0] < lifetime:
                    METRICS.record("Cache " + provider_group(str(url)), hit=True)
                    return cached_response(cached)
        METRICS.record("Cache " + provider_group(str(url)), hit=False)
        response = _ORIGINAL_REQUESTS_GET(url, *args, **kwargs)
        response.openkiri_fetched_at = time.time()
        if ttl and response.status_code in {200, 401, 403, 404, 429, 502, 503} and len(response.content) <= 2_000_000:
            record = (response.openkiri_fetched_at, response.status_code, response.content, dict(response.headers), response.url, response.encoding)
            with _CACHE_LOCK:
                HTTP_GET_CACHE[key] = record
                cache_prune(HTTP_GET_CACHE, 256)
        return response


def measured_session_send(session, request, **kwargs):
    started = time.monotonic()
    # Only aggregate sizes; prepared URLs, cookies and credentials are never retained.
    body = request.body
    sent = len(str(request.method)) + len(str(request.path_url).encode()) + 16
    sent += sum(len(str(k).encode()) + len(str(v).encode()) + 4 for k, v in request.headers.items())
    if isinstance(body, (bytes, str)):
        sent += len(body.encode() if isinstance(body, str) else body)
    status, received = 599, 0
    try:
        response = _ORIGINAL_SESSION_SEND(session, request, **kwargs)
        status = response.status_code
        if not kwargs.get("stream"):
            received = len(response.content)
        return response
    finally:
        METRICS.record("Upstream " + provider_group(request.url), elapsed=time.monotonic() - started,
                       status=status, sent=sent, received=received)


def copy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def cached_price_history(symbol: str, period: str, interval: str) -> list[dict[str, Any]]:
    key = (symbol.upper(), period, interval)
    now = time.time()
    ttl = history_ttl(period, interval)
    with _CACHE_LOCK:
        cached = PRICE_HISTORY_CACHE.get(key)
        if cached and now - cached[0] < ttl:
            METRICS.record("Cache price history", hit=True)
            return copy_rows(cached[1])
    METRICS.record("Cache price history", hit=False)
    rows = _ORIGINAL_FETCH_PRICE_HISTORY(symbol, period, interval)
    with _CACHE_LOCK:
        PRICE_HISTORY_CACHE[key] = (now, copy_rows(rows))
        cache_prune(PRICE_HISTORY_CACHE, 384)
    return rows


def cached_fetch_news(symbol: str, market: str) -> dict[str, Any]:
    key = (symbol.upper(), market)
    now = time.time()
    with _CACHE_LOCK:
        cached = NEWS_CACHE.get(key)
        if cached and now - cached[0] < 1800:
            return dict(cached[1])
    payload = _ORIGINAL_FETCH_NEWS(symbol, market)
    with _CACHE_LOCK:
        NEWS_CACHE[key] = (now, dict(payload))
        cache_prune(NEWS_CACHE, 160)
    return payload


def cached_macro_snapshot() -> dict[str, Any]:
    global MACRO_CACHE
    now = time.time()
    if MACRO_CACHE and now - MACRO_CACHE[0] < 900:
        return dict(MACRO_CACHE[1])
    payload = _ORIGINAL_FETCH_MACRO_SNAPSHOT()
    MACRO_CACHE = (now, dict(payload))
    return payload


requests.get = bandwidth_cached_get
requests.Session.send = measured_session_send
base.requests.get = bandwidth_cached_get
base.fetch_price_history = cached_price_history
base.fetch_news = cached_fetch_news
base.fetch_macro_snapshot = cached_macro_snapshot

NEUTRAL_NEWS: dict[str, Any] = {
    "query": "technical-only",
    "label": "neutral",
    "score": 0,
    "positive": 0,
    "negative": 0,
    "items": [],
}

NEUTRAL_EVENT_CONTEXT: dict[str, Any] = {
    "risk_points": 0,
    "confidence_penalty": 0,
    "direction_edge": 0,
    "summary": "News, earnings and macro lookups are not loaded in technical-only analysis.",
    "flags": [],
    "earnings": [],
    "macro": [],
    "earnings_news": [],
}

NEUTRAL_MACRO: dict[str, Any] = {"label": "mixed", "items": {}}


def remove_route(path: str, methods: set[str] | None = None) -> None:
    methods = methods or {"GET"}
    app.router.routes = [
        route for route in app.router.routes
        if not (getattr(route, "path", "") == path and methods.intersection(set(getattr(route, "methods", []) or [])))
    ]


for route_path in {"/", "/api/analyze", "/api/quote", "/api/quote/{symbol}", "/api/movers", "/api/version", "/api/recommendations", "/api/tw/market-pulse"}:
    remove_route(route_path)


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    script = '<script src="/static/openkiri-live.js?v=20261010-workspace"></script>'
    if script not in html:
        html = html.replace("</body>", f"{script}\n</body>")
    return HTMLResponse(html)


@app.get("/api/version")
def version() -> dict[str, Any]:
    return {
        "ok": True,
        "app": "OpenKiri",
        "entry": "openkiri_live",
        "commit_marker": "public-valuation-sources-20261011.2",
        "fast_intraday": True,
        "render_service": "stock-risk-radar",
    }


@app.get("/api/diagnostics")
def diagnostics() -> dict[str, Any]:
    return METRICS.snapshot()


@app.get("/api/tw/market-pulse")
def tw_market_pulse() -> dict[str, Any]:
    global MARKET_PULSE_SAVER_CACHE
    now = time.time()
    if MARKET_PULSE_SAVER_CACHE and now - MARKET_PULSE_SAVER_CACHE[0] < 180:
        return MARKET_PULSE_SAVER_CACHE[1]
    payload = base.market_pulse_payload()
    MARKET_PULSE_SAVER_CACHE = (now, payload)
    return payload


@app.get("/api/recommendations")
def recommendations(
    markets: str = Query("US,TW", max_length=16),
    limit: int = Query(8, ge=3, le=12),
) -> dict[str, Any]:
    selected = ",".join(sorted({part.strip().upper() for part in markets.split(",") if part.strip()}))
    cache_key = (selected, limit)
    now = time.time()
    cached = RECOMMENDATIONS_CACHE.get(cache_key)
    if cached and now - cached[0] < 900:
        return cached[1]
    payload = base.recommendations(markets=selected or markets, limit=limit)
    payload["bandwidth_saver"] = "cached for 15 minutes"
    RECOMMENDATIONS_CACHE[cache_key] = (now, payload)
    cache_prune(RECOMMENDATIONS_CACHE, 24)
    return payload


@app.get("/api/analyze")
def analyze(
    symbol: str = Query(..., min_length=1, max_length=32),
    period: str = Query("1y", pattern="^(1d|5d|1mo|6mo|1y|2y|5y)$"),
    interval: str = Query("1d", pattern="^(5m|15m|1h|1d|1wk)$"),
    context: bool = Query(False),
) -> dict[str, Any]:
    period, interval = base.normalize_period_interval(period, interval)
    raw = symbol.strip().upper()
    normalized, market = base.normalize_symbol(raw)
    requested_period, requested_interval = period, interval
    fast_intraday = period in {"1d", "5d"} and interval in {"5m", "15m", "1h"}
    include_context = context is True
    cache_key = (normalized, period, interval, include_context)
    now = time.time()
    cached = ANALYZE_CACHE.get(cache_key)
    if cached and now - cached[0] < analyze_ttl(period, interval):
        METRICS.record("Cache analysis", hit=True)
        return cached[1]
    METRICS.record("Cache analysis", hit=False)

    rows: list[dict[str, Any]] = []
    resolved = normalized

    for candidate in base.candidate_symbols(normalized, raw):
        for try_period, try_interval in history_variants(period, interval):
            rows = base.fetch_price_history(candidate, try_period, try_interval)
            if len(rows) >= minimum_history_bars(try_interval):
                resolved, period, interval = candidate, try_period, try_interval
                break
        if rows and len(rows) >= minimum_history_bars(interval):
            break
    if len(rows) < minimum_history_bars(interval):
        raise HTTPException(status_code=422, detail="Not enough price data.")

    rows = base.calculate_indicators(rows)
    latest, previous = rows[-1], rows[-2]
    levels = base.support_resistance(rows)
    if not include_context:
        news = dict(NEUTRAL_NEWS)
        event_context = dict(NEUTRAL_EVENT_CONTEXT)
        macro = dict(NEUTRAL_MACRO)
    else:
        news = base.fetch_news(resolved, market)
        event_context = base.build_event_context(resolved, market, news)
        macro = base.fetch_macro_snapshot()
    risk = base.build_risk(latest, levels, news, event_context)
    suitability = base.build_suitability(latest, risk, news)
    prediction = base.build_prediction(rows, latest, risk, news, event_context)
    visible_rows = rows[-180:]
    chart_math = base.build_chart_math(visible_rows, latest, risk, prediction)
    design_signals = base.build_design_signals(resolved, market, rows, latest, levels, risk, prediction, news, macro)
    # Fundamentals load separately so an unavailable provider cannot block the chart.
    valuation = empty_valuation(resolved, latest.get("_meta", {}).get("currency"))
    chart_meta = provenance("Yahoo chart", latest.get("date"), latest.get("_meta", {}).get("fetched_at"),
                            symbol=resolved, currency=latest.get("_meta", {}).get("currency"), interval=interval,
                            time_basis="bar_start", status="delayed_or_historical")

    response = {
        "ok": True,
        "symbol": resolved,
        "input_symbol": raw,
        "market": market,
        "period": period,
        "interval": interval,
        "requested_period": requested_period,
        "requested_interval": requested_interval,
        "data_fallback": period != requested_period or interval != requested_interval,
        "fast_intraday": fast_intraday,
        "analysis_scope": "technical_news_events" if include_context else "technical_only",
        "provenance": {"chart": chart_meta, "price": chart_meta},
        "latest": {
            "date": latest["date_label"],
            "open": base.number(latest["open"]),
            "high": base.number(latest["high"]),
            "low": base.number(latest["low"]),
            "close": base.number(latest["close"]),
            "volume": int(latest["volume"] or 0),
        },
        "change": {
            "amount": base.number(latest["close"] - previous["close"]),
            "pct": base.number((latest["close"] / previous["close"] - 1) * 100) if previous["close"] else 0,
        },
        "technical": base.technical_payload(latest),
        "levels": levels,
        "risk": risk,
        "suitability": suitability,
        "prediction": prediction,
        "news": news,
        "event_context": event_context,
        "macro": macro,
        "design_signals": design_signals,
        "chart_math": chart_math,
        "valuation": valuation,
        "chart": base.build_chart_rows(visible_rows, market),
    }
    ANALYZE_CACHE[cache_key] = (now, response)
    cache_prune(ANALYZE_CACHE, 160)
    if not fast_intraday:
        base.save_log(raw, resolved, market, response)
    return response


@app.get("/api/quote/{symbol}")
def quote(symbol: str) -> dict[str, Any]:
    return latest_quote_payload(symbol)


def saver_universe(selected: set[str], mode: str, cap: int) -> list[dict[str, Any]]:
    preferred = set(base.DAYTRADE_DEFAULT_SYMBOLS)
    preferred.update({"NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "MU", "AVGO", "AMD"})
    rows = [item for item in base.STOCK_UNIVERSE if item["market"] in selected]
    rows.sort(key=lambda item: (item["symbol"] not in preferred, -float(item.get("market_cap_usd") or 0)))
    per_market = 8 if mode == "live" else 12
    out: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for item in rows:
        market = item["market"]
        if counts.get(market, 0) >= per_market:
            continue
        out.append(item)
        counts[market] = counts.get(market, 0) + 1
        if len(out) >= cap:
            break
    return out


@app.get("/api/movers")
def movers(
    markets: str = Query("US,TW", max_length=16),
    limit: int = Query(8, ge=3, le=12),
    mode: str = Query("recent", pattern="^(recent|live)$"),
) -> dict[str, Any]:
    selected = {part.strip().upper() for part in markets.split(",") if part.strip()}
    cache_key = (",".join(sorted(selected)), limit, mode)
    now_ts = datetime.now(timezone.utc).timestamp()
    ttl = 180 if mode == "live" else 300
    cached = base.MOVERS_CACHE.get(cache_key)
    if cached and now_ts - cached[0] < ttl:
        return cached[1]

    rows = []
    items = saver_universe(selected, mode, 16 if mode == "live" else 24)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(base.mover_quote, item, mode=mode): item for item in items}
        for future in as_completed(futures):
            try:
                row = future.result()
            except Exception:
                row = None
            if not row:
                continue
            row["model_score"] = daytrade_mover_score(row)
            row["side"] = "long" if row["model_score"] >= 8 else "short" if row["model_score"] <= -8 else "watch"
            rows.append(row)
    rows.sort(key=lambda row: row["change_pct"], reverse=True)
    payload = {
        "ok": True,
        "mode": mode,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "markets": sorted(selected),
        "scanned": len(rows),
        "gainers": rows[:limit],
        "losers": sorted(rows, key=lambda row: row["change_pct"])[:limit],
        "long_candidates": sorted([r for r in rows if r["model_score"] > 0], key=lambda r: r["model_score"], reverse=True)[:limit],
        "short_candidates": sorted([r for r in rows if r["model_score"] < 0], key=lambda r: r["model_score"])[:limit],
    }
    base.MOVERS_CACHE[cache_key] = (now_ts, payload)
    cache_prune(base.MOVERS_CACHE, 64)
    return payload


@app.get("/api/alerts")
def alerts(markets: str = Query("TW", max_length=16), limit: int = Query(8, ge=3, le=20)) -> dict[str, Any]:
    selected = {part.strip().upper() for part in markets.split(",") if part.strip()}
    cache_key = (",".join(sorted(selected)), limit)
    now = time.time()
    cached = ALERT_CACHE.get(cache_key)
    if cached and now - cached[0] < 600:
        return cached[1]

    pool: list[dict[str, Any]] = []
    items = saver_universe(selected, "live", 18)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(stock_alert_signal, item): item for item in items}
        for future in as_completed(futures):
            try:
                signal = future.result()
            except Exception:
                signal = None
            if signal:
                pool.append(signal)
    pool.sort(key=lambda item: (item["priority"], abs(item["score"])), reverse=True)
    payload = {
        "ok": True,
        "markets": sorted(selected),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "items": pool[:limit],
        "model": "5m MA cross + moving-average topology + live quote pulse",
    }
    ALERT_CACHE[cache_key] = (now, payload)
    cache_prune(ALERT_CACHE, 32)
    return payload


def minimum_history_bars(interval: str) -> int:
    return 6 if interval in {"1m", "5m", "15m", "1h"} else 20


def history_variants(period: str, interval: str) -> list[tuple[str, str]]:
    variants = [(period, interval)]
    if interval == "5m":
        variants.extend([("5d", "5m"), ("1mo", "15m"), ("5d", "1d"), ("1mo", "1d"), ("6mo", "1d")])
    elif interval == "15m":
        variants.extend([("5d", "15m"), ("1mo", "15m"), ("5d", "1d"), ("1mo", "1d"), ("6mo", "1d")])
    elif interval == "1h":
        variants.extend([("1mo", "1h"), ("5d", "1d"), ("1mo", "1d"), ("6mo", "1d")])
    elif period == "1d":
        variants.extend([("5d", "1d"), ("1mo", "1d"), ("6mo", "1d")])
    out: list[tuple[str, str]] = []
    for item in variants:
        if item not in out:
            out.append(item)
    return out


def same_resolved_symbol(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return base.normalize_symbol(str(left))[0] == base.normalize_symbol(str(right))[0]


def fetch_yahoo_quote_snapshot(symbol: str) -> dict[str, Any]:
    try:
        resp = requests.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": symbol, "formatted": "false"},
            headers={"User-Agent": "Mozilla/5.0 OpenKiri/5.0"}, timeout=4,
        )
        resp.raise_for_status()
        rows = (resp.json().get("quoteResponse") or {}).get("result") or []
        row = next((row for row in rows if same_resolved_symbol(row.get("symbol"), symbol)), None)
        if not row:
            return {}
        snapshot = {key: finite(row.get(field)) for key, field in {
            "price": "regularMarketPrice", "open": "regularMarketOpen",
            "high": "regularMarketDayHigh", "low": "regularMarketDayLow",
            "change": "regularMarketChange", "change_pct": "regularMarketChangePercent",
            "volume": "regularMarketVolume", "market_cap": "marketCap",
            "shares_outstanding": "sharesOutstanding", "trailing_pe": "trailingPE", "forward_pe": "forwardPE",
        }.items()}
        snapshot.update(provenance("Yahoo quote", row.get("regularMarketTime"),
                                   getattr(resp, "openkiri_fetched_at", None)))
        snapshot.update({"symbol": row["symbol"], "currency": row.get("currency"),
                         "market_state": row.get("marketState"), "date": snapshot["as_of"]})
        return snapshot
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return {}


def latest_quote_payload(symbol: str) -> dict[str, Any]:
    raw = symbol.strip().upper()
    normalized, market = base.normalize_symbol(raw)
    key = (normalized,)
    with _QUOTE_LOCKS[hash(key) % len(_QUOTE_LOCKS)]:
        cached = base.QUOTE_CACHE.get(key)
        if cached and time.time() - cached[0] < 45:
            METRICS.record("Cache quote payload", hit=True)
            return cached[1]
        METRICS.record("Cache quote payload", hit=False)
        for candidate in base.candidate_symbols(normalized, raw):
            snapshot = fetch_yahoo_quote_snapshot(candidate)
            fallback = False
            if not finite(snapshot.get("price"), positive=True):
                rows = base.fetch_price_history(candidate, "1d", "1m")
                interval = "1m"
                if not rows:
                    rows = base.fetch_price_history(candidate, "5d", "1d")
                    interval, fallback = "1d", True
                if not rows:
                    continue
                latest = rows[-1]
                meta = latest.get("_meta", {})
                price, timestamp = latest["close"], latest["date"]
                meta_price = finite(meta.get("regularMarketPrice"), positive=True)
                meta_time = finite(meta.get("regularMarketTime"))
                if meta_price and meta_time and meta_time >= latest["date"].timestamp():
                    price, timestamp = meta_price, meta_time
                previous = finite(meta.get("chartPreviousClose"), positive=True) if interval == "1m" else (
                    rows[-2]["close"] if len(rows) > 1 else None)
                snapshot = provenance("Yahoo chart", timestamp, meta.get("fetched_at"),
                                      status="delayed_or_historical", interval=interval)
                snapshot.update({"symbol": candidate, "price": price, "currency": meta.get("currency"),
                                 "open": rows[0]["open"] if interval == "1m" else latest["open"],
                                 "high": max(r["high"] for r in rows) if interval == "1m" else latest["high"],
                                 "low": min(r["low"] for r in rows) if interval == "1m" else latest["low"],
                                 "volume": sum(r["volume"] for r in rows) if interval == "1m" else latest["volume"],
                                 "change": price - previous if previous else None,
                                 "change_pct": (price / previous - 1) * 100 if previous else None})
            valuation = valuation_from_snapshot(candidate, snapshot)
            payload = {
                "ok": True, "symbol": candidate, "input_symbol": raw, "market": market,
                **{key: snapshot.get(key) for key in ("price", "open", "high", "low", "change", "change_pct", "volume", "currency", "market_state")},
                "date": snapshot.get("as_of"), "source": snapshot.get("source"),
                "as_of": snapshot.get("as_of"), "fetched_at": snapshot.get("fetched_at"),
                "provenance": {"price": provenance(snapshot.get("source"), snapshot.get("as_of"), snapshot.get("fetched_at"),
                                                   status="delayed_or_historical")},
                "valuation": valuation, "volume_ratio": None, "fallback": fallback,
                "data_warning": "Provider data may be delayed; fetched_at is not the trade time.",
                **{key: valuation.get(key) for key in ("market_cap", "market_cap_previous", "market_cap_change_pct", "trailing_pe", "forward_pe")},
            }
            base.QUOTE_CACHE[key] = (time.time(), payload)
            cache_prune(base.QUOTE_CACHE, 256)
            return payload
    raise HTTPException(status_code=404, detail=f"No verified quote data for {raw}.")


@app.get("/api/quote")
def quote_query(symbol: str = Query(..., min_length=1, max_length=32)) -> dict[str, Any]:
    return latest_quote_payload(symbol)


def verified_valuation(symbol: str) -> dict[str, Any]:
    normalized, market = base.normalize_symbol(symbol)
    with _VALUATION_LOCKS[hash(normalized) % len(_VALUATION_LOCKS)]:
        cached = VALUATION_CACHE.get(normalized)
        if cached and time.time() - cached[0] < 900:
            METRICS.record("Cache valuation", hit=True)
            return dict(cached[1])
        METRICS.record("Cache valuation", hit=False)
        payload = public_valuation(normalized)
        if payload["status"] == "unavailable":
            payload = valuation_from_snapshot(normalized, fetch_yahoo_quote_snapshot(normalized))
        payload["fetched_at"] = payload.get("fetched_at") or iso_time(time.time())
        VALUATION_CACHE[normalized] = (time.time(), payload)
        cache_prune(VALUATION_CACHE, 256)
        return dict(payload)


@app.get("/api/valuation")
def valuation_query(symbol: str = Query(..., min_length=1, max_length=32)) -> dict[str, Any]:
    payload = verified_valuation(symbol)
    return {"ok": True, "symbol": payload["symbol"], "valuation": payload}


def verified_stock_valuation(item: dict[str, Any], latest_price: float) -> dict[str, Any]:
    payload = verified_valuation(item["symbol"])
    cap = payload.get("market_cap")
    payload["market_cap_usd"] = int(cap) if cap and payload.get("currency") == "USD" else 0
    if cap and payload.get("currency") == "TWD":
        fx = base.fetch_price_history("TWD=X", "5d", "1d")
        rate = finite(fx[-1]["close"], positive=True) if fx else None
        if rate:
            payload["market_cap_usd"] = int(cap / rate)
            payload["fx_as_of"] = iso_time(fx[-1]["date"])
    return payload


# Legacy screeners and T+0 routes share the same identity/provenance contract.
base.latest_quote_payload = latest_quote_payload
base.fetch_yahoo_quote_snapshot = fetch_yahoo_quote_snapshot
base.fetch_yahoo_valuation = verified_valuation
base.stock_valuation_payload = verified_stock_valuation


def daytrade_mover_score(row: dict[str, Any]) -> float:
    change = float(row.get("change_pct") or 0)
    pattern = str(row.get("pattern") or "")
    pattern_score = float(row.get("pattern_score") or 0)
    score = change * 8
    if pattern in {"breakout", "v_rebound"}:
        score += min(18, abs(pattern_score) * 1.8)
    if pattern in {"selloff", "inverse_v"}:
        score -= min(18, abs(pattern_score) * 1.8)
    if row.get("volume"):
        score += min(6, math.log10(max(10, float(row["volume"]))) - 4)
    return base.number(score, 2)


def stock_alert_signal(item: dict[str, Any]) -> dict[str, Any] | None:
    rows = base.fetch_price_history(item["symbol"], "5d", "5m")
    interval = "5m"
    if len(rows) < 20:
        rows = base.fetch_price_history(item["symbol"], "6mo", "1d")
        interval = "1d"
    if len(rows) < 20:
        return None
    rows = base.calculate_indicators(rows)
    latest = rows[-1]
    neutral_news = {"label": "neutral", "positive": 0, "negative": 0, "score": 0, "items": []}
    levels = base.support_resistance(rows)
    risk = base.build_risk(latest, levels, neutral_news)
    prediction = base.build_prediction(rows, latest, risk, neutral_news)
    chart_math = base.build_chart_math(rows[-180:], latest, risk, prediction)
    cross = chart_math.get("latest_cross") or {}
    bars = chart_math.get("bars_since_cross")
    score = int(chart_math.get("confidence") or 0)
    change_pct = latest.get("RET1", 0) * 100
    kind = ""
    title = ""
    action = ""
    priority = 0
    fresh_limit = 12 if interval == "5m" else 2
    if cross.get("type") == "golden_cross" and bars is not None and bars <= fresh_limit:
        kind, title, priority = "golden_cross", "黃金交叉", 4
        action = "偏多觀察：先看量能與回測 MA20/MA25 是否守住。"
    elif cross.get("type") == "death_cross" and bars is not None and bars <= fresh_limit:
        kind, title, priority = "death_cross", "死亡交叉", 4
        action = "偏空/風險升高：避免追多，短線看是否跌破均線堆疊。"
    elif chart_math.get("verdict") == "bullish_continuation" and score >= 58:
        kind, title, priority = "bullish_continuation", "多頭延續", 2
        action = "偏多分類：適合放入 T+0 觀察名單，不等於直接買進。"
    elif chart_math.get("verdict") == "bearish_continuation" and score >= 58:
        kind, title, priority = "bearish_continuation", "空頭延續", 2
        action = "偏空分類：適合觀察轉弱或避開追高。"
    if not kind:
        return None
    clean_copy = {
        "golden_cross": ("黃金交叉", "偏多觀察：先確認價格站穩 MA20/MA25，量能同步放大再考慮。"),
        "death_cross": ("死亡交叉", "偏空警示：避免追價，等重新站回短均或賣壓縮小再評估。"),
        "bullish_continuation": ("多頭延續", "偏多候選：留意回測均線不破與成交量是否延續。"),
        "bearish_continuation": ("空頭延續", "偏空候選：留意反彈無力與跌破日內支撐。"),
    }
    title, action = clean_copy.get(kind, (title, action))
    ascii_safe_copy = {
        "golden_cross": ("\u9ec3\u91d1\u4ea4\u53c9", "\u504f\u591a\u89c0\u5bdf\uff1a\u5148\u78ba\u8a8d\u50f9\u683c\u7ad9\u7a69 MA20/MA25\uff0c\u91cf\u80fd\u540c\u6b65\u653e\u5927\u518d\u8003\u616e\u3002"),
        "death_cross": ("\u6b7b\u4ea1\u4ea4\u53c9", "\u504f\u7a7a\u8b66\u793a\uff1a\u907f\u514d\u8ffd\u50f9\uff0c\u7b49\u91cd\u65b0\u7ad9\u56de\u77ed\u5747\u6216\u8ce3\u58d3\u7e2e\u5c0f\u518d\u8a55\u4f30\u3002"),
        "bullish_continuation": ("\u591a\u982d\u5ef6\u7e8c", "\u504f\u591a\u5019\u9078\uff1a\u7559\u610f\u56de\u6e2c\u5747\u7dda\u4e0d\u7834\u8207\u6210\u4ea4\u91cf\u662f\u5426\u5ef6\u7e8c\u3002"),
        "bearish_continuation": ("\u7a7a\u982d\u5ef6\u7e8c", "\u504f\u7a7a\u5019\u9078\uff1a\u7559\u610f\u53cd\u5f48\u7121\u529b\u8207\u8dcc\u7834\u65e5\u5167\u652f\u6490\u3002"),
    }
    title, action = ascii_safe_copy.get(kind, (title, action))
    return {
        "symbol": item["symbol"],
        "name": item.get("name"),
        "market": item.get("market"),
        "kind": kind,
        "title": title,
        "action": action,
        "score": score,
        "priority": priority,
        "interval": interval,
        "bars_since_cross": bars,
        "price": base.number(latest["close"]),
        "change_pct": base.number(change_pct),
        "date": latest.get("date_label"),
    }
