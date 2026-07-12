from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse

import app as base

app = base.app
BASE_DIR = Path(__file__).resolve().parent
ALERT_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
USD_TWD_CACHE: tuple[float, float] | None = None
ANALYZE_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
GOOGLE_FINANCE_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

NEUTRAL_NEWS: dict[str, Any] = {
    "query": "intraday-fast-mode",
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
    "summary": "Intraday fast mode skips slow earnings and macro lookups.",
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


for route_path in {"/", "/api/analyze", "/api/quote", "/api/quote/{symbol}", "/api/movers", "/api/version"}:
    remove_route(route_path)


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    script = '<script src="/static/openkiri-live.js?v=20260712-bandwidth"></script>'
    if script not in html:
        html = html.replace("</body>", f"{script}\n</body>")
    return HTMLResponse(html)


@app.get("/api/version")
def version() -> dict[str, Any]:
    return {
        "ok": True,
        "app": "OpenKiri",
        "entry": "openkiri_live",
        "commit_marker": "bandwidth-saver-lazy-load",
        "fast_intraday": True,
        "render_service": "stock-risk-radar",
    }


@app.get("/api/analyze")
def analyze(
    symbol: str = Query(..., min_length=1, max_length=32),
    period: str = Query("1y", pattern="^(1d|5d|1mo|6mo|1y|2y|5y)$"),
    interval: str = Query("1d", pattern="^(5m|15m|1h|1d|1wk)$"),
) -> dict[str, Any]:
    period, interval = base.normalize_period_interval(period, interval)
    raw = symbol.strip().upper()
    normalized, market = base.normalize_symbol(raw)
    requested_period, requested_interval = period, interval
    fast_intraday = period in {"1d", "5d"} and interval in {"5m", "15m", "1h"}
    cache_key = (normalized, period, interval)
    now = time.time()
    cached = ANALYZE_CACHE.get(cache_key)
    if fast_intraday and cached and now - cached[0] < 10:
        return cached[1]

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
    if fast_intraday:
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
    universe_item = base.find_universe_item(resolved) or {"symbol": resolved, "name": resolved, "market": market, "industry": "Unknown", "market_cap_usd": 0}
    if fast_intraday:
        valuation = fast_valuation_placeholder(universe_item, market)
    else:
        valuation = base.stock_valuation_payload(universe_item, latest["close"])
        valuation = improve_valuation_with_quote(resolved, market, valuation)

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
    if fast_intraday:
        ANALYZE_CACHE[cache_key] = (now, response)
    else:
        base.save_log(raw, resolved, market, response)
    return response


@app.get("/api/quote/{symbol}")
def quote(symbol: str) -> dict[str, Any]:
    return latest_quote_payload(symbol)


@app.get("/api/movers")
def movers(
    markets: str = Query("US,TW", max_length=16),
    limit: int = Query(8, ge=3, le=12),
    mode: str = Query("recent", pattern="^(recent|live)$"),
) -> dict[str, Any]:
    selected = {part.strip().upper() for part in markets.split(",") if part.strip()}
    cache_key = (",".join(sorted(selected)), limit, mode)
    now_ts = datetime.now(timezone.utc).timestamp()
    ttl = 8 if mode == "live" else 60
    cached = base.MOVERS_CACHE.get(cache_key)
    if cached and now_ts - cached[0] < ttl:
        return cached[1]

    rows = []
    items = [item for item in base.STOCK_UNIVERSE if item["market"] in selected]
    with ThreadPoolExecutor(max_workers=10) as executor:
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
    return payload


@app.get("/api/alerts")
def alerts(markets: str = Query("TW", max_length=16), limit: int = Query(8, ge=3, le=20)) -> dict[str, Any]:
    selected = {part.strip().upper() for part in markets.split(",") if part.strip()}
    cache_key = (",".join(sorted(selected)), limit)
    now = time.time()
    cached = ALERT_CACHE.get(cache_key)
    if cached and now - cached[0] < 45:
        return cached[1]

    pool: list[dict[str, Any]] = []
    items = [row for row in base.STOCK_UNIVERSE if row["market"] in selected][:48]
    with ThreadPoolExecutor(max_workers=8) as executor:
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


def usd_twd_rate() -> float:
    global USD_TWD_CACHE
    now = time.time()
    if USD_TWD_CACHE and now - USD_TWD_CACHE[0] < 1800:
        return USD_TWD_CACHE[1]
    rate = 31.8
    try:
        rate = float(base.quote_last("TWD=X").get("price") or rate)
    except Exception:
        pass
    USD_TWD_CACHE = (now, rate)
    return rate


def fast_valuation_placeholder(item: dict[str, Any], market: str) -> dict[str, Any]:
    cap_usd = base.optional_number((item or {}).get("market_cap_usd"), 0)
    if not cap_usd:
        return {"market_cap": None, "currency": "TWD" if market == "TW" else "USD", "fallback": True, "source": "fast placeholder"}
    if market == "TW":
        return {
            "market_cap": int(cap_usd * 31.8),
            "currency": "TWD",
            "market_cap_usd": int(cap_usd),
            "fallback": True,
            "source": "fast built-in estimate; Google Finance quote refresh follows",
        }
    return {
        "market_cap": int(cap_usd),
        "currency": "USD",
        "market_cap_usd": int(cap_usd),
        "fallback": True,
        "source": "fast built-in estimate; Google Finance quote refresh follows",
    }


def market_cap_fallback(symbol: str, market: str, live_price: float | None, currency: str | None) -> tuple[int | None, str | None, float | None, str | None, dict[str, Any]]:
    valuation: dict[str, Any] = {}
    try:
        valuation = base.fetch_yahoo_valuation(symbol)
    except Exception:
        valuation = {}

    resolved_currency = str(valuation.get("currency") or currency or ("TWD" if market == "TW" else "USD")).upper()
    cap = base.optional_number(valuation.get("market_cap"), 0)
    shares = base.optional_number(valuation.get("shares_outstanding"), 0)
    if not cap and shares and live_price:
        cap = shares * live_price
    if cap:
        return int(cap), resolved_currency, shares, valuation.get("source") or "Yahoo valuation fallback", valuation

    item = base.find_universe_item(symbol)
    cap_usd = base.optional_number((item or {}).get("market_cap_usd"), 0)
    if cap_usd:
        if market == "TW" or resolved_currency == "TWD":
            return int(cap_usd * usd_twd_rate()), "TWD", shares, "built-in USD estimate converted to TWD", valuation
        return int(cap_usd), "USD", shares, "built-in USD estimate", valuation

    return None, resolved_currency, shares, valuation.get("source"), valuation


def compact_amount(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip().upper().replace(",", "")
    multiplier = 1
    if text[-1:] in {"K", "M", "B", "T"}:
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}[text[-1]]
        text = text[:-1]
    text = re.sub(r"[^0-9.\-]", "", text)
    if not text:
        return None
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def google_finance_codes(symbol: str, market: str) -> list[tuple[str, str]]:
    plain = symbol.upper().replace(".TW", "").replace(".TWO", "")
    if market == "TW" or symbol.upper().endswith((".TW", ".TWO")) or plain.isdigit():
        if symbol.upper().endswith(".TWO"):
            return [(f"{plain}:TWO", "TWO")]
        if symbol.upper().endswith(".TW"):
            return [(f"{plain}:TPE", "TPE")]
        return [(f"{plain}:TPE", "TPE"), (f"{plain}:TWO", "TWO")]
    return [(f"{plain}:NASDAQ", "NASDAQ"), (f"{plain}:NYSE", "NYSE"), (f"{plain}:NYSEARCA", "NYSEARCA")]


def clean_google_text(html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text))


def google_money(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return compact_amount("".join(group or "" for group in match.groups()))


def fetch_google_finance_snapshot(symbol: str, market: str) -> dict[str, Any]:
    cache_key = (symbol.upper(), market)
    now = time.time()
    cached = GOOGLE_FINANCE_CACHE.get(cache_key)
    if cached and now - cached[0] < 30:
        return cached[1]

    for code, exchange in google_finance_codes(symbol, market):
        try:
            url = f"https://www.google.com/finance/quote/{code}"
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 OpenKiri/4.9"},
                timeout=4,
            )
            resp.raise_for_status()
            text = clean_google_text(resp.text)
            pos = text.find(code)
            if pos < 0:
                continue
            snippet = text[pos : pos + 2400]
            if "arrow_" not in snippet or "Mkt. cap" not in snippet:
                continue

            price_match = re.search(r"((?:NT\$|\$)[0-9][0-9,.]*)\s+arrow_(upward|downward)", snippet)
            price = compact_amount(price_match.group(1)) if price_match else None
            direction = price_match.group(2) if price_match else ""
            pct_match = re.search(r"arrow_(?:upward|downward)\s+([+\-]?[0-9,.]+)%", snippet)
            change_pct = compact_amount(pct_match.group(1)) if pct_match else None
            if change_pct is not None and direction == "downward" and change_pct > 0:
                change_pct *= -1

            payload = {
                "symbol": symbol.upper(),
                "google_code": code,
                "currency": "TWD" if " TWD " in snippet or code.endswith(":TPE") or code.endswith(":TWO") else "USD",
                "price": price,
                "open": google_money(r"Open\s+((?:NT\$|\$)[0-9][0-9,.]*)", snippet),
                "high": google_money(r"High\s+((?:NT\$|\$)[0-9][0-9,.]*)", snippet),
                "low": google_money(r"Low\s+((?:NT\$|\$)[0-9][0-9,.]*)", snippet),
                "change_pct": change_pct,
                "market_cap": compact_amount((re.search(r"Mkt\. cap\s+([0-9][0-9,.]*[KMBT]?)", snippet) or [None, None])[1]),
                "volume": compact_amount((re.search(r"Volume\s+([0-9][0-9,.]*[KMBT]?)", snippet) or [None, None])[1]),
                "shares_outstanding": compact_amount((re.search(r"Shares outstanding\s+([0-9][0-9,.]*[KMBT]?)", snippet) or [None, None])[1]),
                "trailing_pe": compact_amount((re.search(r"P/E ratio\s+([0-9][0-9,.]*)", snippet) or [None, None])[1]),
                "source": "Google Finance",
                "source_url": url,
            }
            GOOGLE_FINANCE_CACHE[cache_key] = (now, payload)
            return payload
        except Exception:
            continue

    GOOGLE_FINANCE_CACHE[cache_key] = (now, {})
    return {}


def same_resolved_symbol(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    left_norm, _ = base.normalize_symbol(str(left))
    right_norm, _ = base.normalize_symbol(str(right))
    return left_norm == right_norm


def fetch_yahoo_quote_snapshot(symbol: str) -> dict[str, Any]:
    try:
        resp = requests.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": symbol, "formatted": "false"},
            headers={"User-Agent": "Mozilla/5.0 OpenKiri/4.8"},
            timeout=8,
        )
        resp.raise_for_status()
        row = ((resp.json().get("quoteResponse") or {}).get("result") or [None])[0] or {}
        if not row:
            return {}
        returned_symbol = row.get("symbol") or symbol
        if not same_resolved_symbol(returned_symbol, symbol):
            return {}
        ts = row.get("regularMarketTime")
        date_text = ""
        if ts:
            try:
                date_text = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_text = ""
        return {
            "symbol": row.get("symbol") or symbol,
            "currency": row.get("currency"),
            "price": base.optional_number(row.get("regularMarketPrice")),
            "open": base.optional_number(row.get("regularMarketOpen")),
            "high": base.optional_number(row.get("regularMarketDayHigh")),
            "low": base.optional_number(row.get("regularMarketDayLow")),
            "change": base.optional_number(row.get("regularMarketChange")),
            "change_pct": base.optional_number(row.get("regularMarketChangePercent")),
            "volume": base.optional_number(row.get("regularMarketVolume")),
            "market_cap": base.optional_number(row.get("marketCap"), 0),
            "shares_outstanding": base.optional_number(row.get("sharesOutstanding")) or base.optional_number(row.get("impliedSharesOutstanding")),
            "trailing_pe": base.optional_number(row.get("trailingPE")),
            "forward_pe": base.optional_number(row.get("forwardPE")),
            "date": date_text,
            "market_state": row.get("marketState"),
            "source": "Yahoo quote",
        }
    except Exception:
        return {}


def latest_quote_payload(symbol: str) -> dict[str, Any]:
    raw = symbol.strip().upper()
    normalized, market = base.normalize_symbol(raw)
    cache_key = (normalized,)
    now = time.time()
    cached = base.QUOTE_CACHE.get(cache_key)
    if cached and now - cached[0] < 6:
        return cached[1]

    tried: list[str] = []
    for candidate in base.candidate_symbols(normalized, raw):
        tried.append(candidate)
        snapshot = fetch_yahoo_quote_snapshot(candidate)
        google_snapshot = fetch_google_finance_snapshot(candidate, market)
        fast_price = base.optional_number((snapshot or {}).get("price"))
        fast_source = "Yahoo quote"
        if fast_price is None:
            fast_price = base.optional_number(google_snapshot.get("price"))
            fast_source = "Google Finance"
        if fast_price is not None and google_snapshot.get("market_cap"):
            fast_change_pct = base.optional_number((snapshot or {}).get("change_pct"))
            if fast_change_pct is None:
                fast_change_pct = base.optional_number(google_snapshot.get("change_pct")) or 0
            previous_price = fast_price / (1 + fast_change_pct / 100) if fast_change_pct > -99 else fast_price
            fast_change = (base.optional_number((snapshot or {}).get("change")) if snapshot else None)
            if fast_change is None:
                fast_change = fast_price - previous_price
            live_market_cap = base.optional_number((snapshot or {}).get("market_cap"), 0) or base.optional_number(google_snapshot.get("market_cap"), 0)
            cap_previous = int(live_market_cap / (1 + fast_change_pct / 100)) if live_market_cap and fast_change_pct > -99 else None
            payload = {
                "ok": True,
                "symbol": candidate,
                "input_symbol": raw,
                "market": market,
                "date": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "price": base.number(fast_price),
                "open": base.number(google_snapshot.get("open") or (snapshot or {}).get("open") or fast_price),
                "high": base.number(google_snapshot.get("high") or (snapshot or {}).get("high") or fast_price),
                "low": base.number(google_snapshot.get("low") or (snapshot or {}).get("low") or fast_price),
                "change": base.number(fast_change),
                "change_pct": base.number(fast_change_pct),
                "volume": int(google_snapshot.get("volume") or (snapshot or {}).get("volume") or 0),
                "volume_ratio": 1,
                "currency": (snapshot or {}).get("currency") or google_snapshot.get("currency") or ("TWD" if market == "TW" else "USD"),
                "market_cap": int(live_market_cap) if live_market_cap else None,
                "market_cap_previous": cap_previous,
                "market_cap_change_pct": base.number(fast_change_pct),
                "shares_outstanding": (snapshot or {}).get("shares_outstanding") or google_snapshot.get("shares_outstanding"),
                "trailing_pe": (snapshot or {}).get("trailing_pe") or google_snapshot.get("trailing_pe"),
                "forward_pe": (snapshot or {}).get("forward_pe"),
                "source": fast_source if fast_source == "Google Finance" else "Yahoo quote + Google Finance",
                "fallback": False,
                "data_warning": "Quote is optimized for low latency; chart bars are loaded by /api/analyze.",
            }
            base.QUOTE_CACHE[cache_key] = (now, payload)
            return payload
        intraday_rows = base.fetch_price_history(candidate, "1d", "1m")
        source = "1d/1m"
        if not intraday_rows:
            intraday_rows = base.fetch_price_history(candidate, "1d", "5m")
            source = "1d/5m fallback"
        daily_rows = base.fetch_price_history(candidate, "5d", "1d")
        rows = intraday_rows or daily_rows
        if not rows:
            if not snapshot:
                continue
            rows = [{
                "date": datetime.now(timezone.utc),
                "date_label": snapshot.get("date") or datetime.now(timezone.utc).isoformat(timespec="minutes"),
                "open": snapshot.get("open") or snapshot.get("price") or 0,
                "high": snapshot.get("high") or snapshot.get("price") or 0,
                "low": snapshot.get("low") or snapshot.get("price") or 0,
                "close": snapshot.get("price") or 0,
                "volume": snapshot.get("volume") or 0,
            }]
        latest = rows[-1]
        previous_close = daily_rows[-2]["close"] if len(daily_rows) >= 2 else rows[-2]["close"] if len(rows) >= 2 else rows[0]["open"]
        live_price = base.optional_number(snapshot.get("price")) if snapshot else None
        if live_price is None:
            live_price = base.optional_number(google_snapshot.get("price")) if google_snapshot else None
        live_change = base.optional_number(snapshot.get("change")) if snapshot else None
        live_change_pct = base.optional_number(snapshot.get("change_pct")) if snapshot else None
        if live_change_pct is None:
            live_change_pct = base.optional_number(google_snapshot.get("change_pct")) if google_snapshot else None
        live_market_cap = base.optional_number(snapshot.get("market_cap"), 0) if snapshot else None
        if not live_market_cap:
            live_market_cap = base.optional_number(google_snapshot.get("market_cap"), 0) if google_snapshot else None
        shares = base.optional_number(snapshot.get("shares_outstanding"), 0) if snapshot else None
        shares = shares or (base.optional_number(google_snapshot.get("shares_outstanding"), 0) if google_snapshot else None)
        if not live_market_cap and shares and live_price:
            live_market_cap = shares * live_price
        currency = (snapshot.get("currency") if snapshot else None) or google_snapshot.get("currency") or ("TWD" if market == "TW" else "USD")
        cap_source = "Google Finance" if google_snapshot.get("market_cap") else None
        valuation_fallback: dict[str, Any] = {}
        if not live_market_cap:
            cap, cap_currency, fallback_shares, cap_source, valuation_fallback = market_cap_fallback(candidate, market, live_price, currency)
            live_market_cap = cap
            currency = cap_currency or currency
            shares = shares or fallback_shares
        if live_price:
            latest["close"] = live_price
        if google_snapshot:
            latest["open"] = google_snapshot.get("open") or latest["open"]
            latest["high"] = google_snapshot.get("high") or latest["high"]
            latest["low"] = google_snapshot.get("low") or latest["low"]
            latest["volume"] = google_snapshot.get("volume") or latest["volume"]
        change = live_change if live_change is not None else latest["close"] - previous_close if previous_close else 0
        change_pct = live_change_pct if live_change_pct is not None else (latest["close"] / previous_close - 1) * 100 if previous_close else 0
        avg_volume = base.mean([row["volume"] for row in rows[-20:-1] if row.get("volume") is not None]) if len(rows) >= 2 else 0
        volume_ratio = latest["volume"] / avg_volume if avg_volume else 1
        cap_previous = int(live_market_cap / (1 + change_pct / 100)) if live_market_cap and change_pct > -99 else None
        source_label = "Yahoo quote live + " + (source if intraday_rows else "5d/1d fallback") if snapshot else source if intraday_rows else "5d/1d fallback"
        if google_snapshot and "Google Finance" not in source_label:
            source_label += " + Google Finance"
        if cap_source and cap_source not in source_label:
            source_label += f" + {cap_source}"
        payload = {
            "ok": True,
            "symbol": candidate,
            "input_symbol": raw,
            "market": market,
            "date": latest["date_label"],
            "price": base.number(latest["close"]),
            "open": base.number(latest["open"]),
            "high": base.number(latest["high"]),
            "low": base.number(latest["low"]),
            "change": base.number(change),
            "change_pct": base.number(change_pct),
            "volume": int(latest["volume"] or 0),
            "volume_ratio": base.number(volume_ratio),
            "currency": currency,
            "market_cap": int(live_market_cap) if live_market_cap else None,
            "market_cap_previous": cap_previous,
            "market_cap_change_pct": base.number(change_pct),
            "shares_outstanding": shares,
            "trailing_pe": (snapshot or {}).get("trailing_pe") or google_snapshot.get("trailing_pe") or valuation_fallback.get("trailing_pe"),
            "forward_pe": (snapshot or {}).get("forward_pe") or valuation_fallback.get("forward_pe"),
            "source": source_label,
            "fallback": (not intraday_rows) or "fallback" in source,
            "data_warning": "Yahoo quote/chart data can be delayed; live 5m falls back to 5d/1d when intraday bars are unavailable.",
        }
        base.QUOTE_CACHE[cache_key] = (now, payload)
        return payload
    raise HTTPException(status_code=404, detail=f"No quote data found for {', '.join(tried) or raw}.")


@app.get("/api/quote")
def quote_query(symbol: str = Query(..., min_length=1, max_length=32)) -> dict[str, Any]:
    return latest_quote_payload(symbol)


def improve_valuation_with_quote(symbol: str, market: str, valuation: dict[str, Any], allow_google: bool = True) -> dict[str, Any]:
    quote = fetch_yahoo_quote_snapshot(symbol)
    google_quote = fetch_google_finance_snapshot(symbol, market) if allow_google else {}
    live_price = quote.get("price") or google_quote.get("price") or valuation.get("price")
    currency = quote.get("currency") or google_quote.get("currency") or valuation.get("currency") or ("TWD" if market == "TW" else "USD")
    cap = base.optional_number(quote.get("market_cap"), 0)
    cap_source = "Yahoo quote live" if cap else None
    if not cap:
        cap = base.optional_number(google_quote.get("market_cap"), 0)
        cap_source = "Google Finance" if cap else None
    shares = base.optional_number(quote.get("shares_outstanding"), 0)
    shares = shares or base.optional_number(google_quote.get("shares_outstanding"), 0)
    fallback: dict[str, Any] = {}
    if not cap:
        cap, fallback_currency, fallback_shares, cap_source, fallback = market_cap_fallback(symbol, market, live_price, currency)
        currency = fallback_currency or currency
        shares = shares or fallback_shares
    if not cap:
        return valuation
    pct = quote.get("change_pct")
    if pct is None:
        pct = google_quote.get("change_pct")
    cap = int(cap)
    valuation = dict(valuation)
    valuation.update({
        "market_cap": cap,
        "currency": currency,
        "market_cap_previous": int(cap / (1 + pct / 100)) if pct is not None and pct > -99 else None,
        "market_cap_change_pct": base.number(pct) if pct is not None else None,
        "shares_outstanding": shares,
        "trailing_pe": quote.get("trailing_pe") or google_quote.get("trailing_pe") or fallback.get("trailing_pe") or valuation.get("trailing_pe"),
        "forward_pe": quote.get("forward_pe") or fallback.get("forward_pe") or valuation.get("forward_pe"),
        "source": cap_source or valuation.get("source"),
    })
    return valuation


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
