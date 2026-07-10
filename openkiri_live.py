from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
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


def remove_route(path: str, methods: set[str] | None = None) -> None:
    methods = methods or {"GET"}
    app.router.routes = [
        route for route in app.router.routes
        if not (getattr(route, "path", "") == path and methods.intersection(set(getattr(route, "methods", []) or [])))
    ]


for route_path in {"/", "/api/analyze", "/api/quote/{symbol}", "/api/movers"}:
    remove_route(route_path)


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    script = '<script src="/static/openkiri-live.js?v=20260709"></script>'
    if script not in html:
        html = html.replace("</body>", f"{script}\n</body>")
    return HTMLResponse(html)


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
    news = base.fetch_news(resolved, market)
    event_context = base.build_event_context(resolved, market, news)
    risk = base.build_risk(latest, levels, news, event_context)
    suitability = base.build_suitability(latest, risk, news)
    prediction = base.build_prediction(rows, latest, risk, news, event_context)
    macro = base.fetch_macro_snapshot()
    visible_rows = rows[-180:]
    chart_math = base.build_chart_math(visible_rows, latest, risk, prediction)
    design_signals = base.build_design_signals(resolved, market, rows, latest, levels, risk, prediction, news, macro)
    universe_item = base.find_universe_item(resolved) or {"symbol": resolved, "name": resolved, "market": market, "industry": "Unknown", "market_cap_usd": 0}
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
        live_change = base.optional_number(snapshot.get("change")) if snapshot else None
        live_change_pct = base.optional_number(snapshot.get("change_pct")) if snapshot else None
        live_market_cap = base.optional_number(snapshot.get("market_cap"), 0) if snapshot else None
        shares = base.optional_number(snapshot.get("shares_outstanding"), 0) if snapshot else None
        if not live_market_cap and shares and live_price:
            live_market_cap = shares * live_price
        currency = snapshot.get("currency") if snapshot else ("TWD" if market == "TW" else "USD")
        cap_source = None
        valuation_fallback: dict[str, Any] = {}
        if not live_market_cap:
            cap, cap_currency, fallback_shares, cap_source, valuation_fallback = market_cap_fallback(candidate, market, live_price, currency)
            live_market_cap = cap
            currency = cap_currency or currency
            shares = shares or fallback_shares
        if live_price:
            latest["close"] = live_price
        change = live_change if live_change is not None else latest["close"] - previous_close if previous_close else 0
        change_pct = live_change_pct if live_change_pct is not None else (latest["close"] / previous_close - 1) * 100 if previous_close else 0
        avg_volume = base.mean([row["volume"] for row in rows[-20:-1] if row.get("volume") is not None]) if len(rows) >= 2 else 0
        volume_ratio = latest["volume"] / avg_volume if avg_volume else 1
        cap_previous = int(live_market_cap / (1 + change_pct / 100)) if live_market_cap and change_pct > -99 else None
        source_label = "Yahoo quote live + " + (source if intraday_rows else "5d/1d fallback") if snapshot else source if intraday_rows else "5d/1d fallback"
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
            "trailing_pe": (snapshot or {}).get("trailing_pe") or valuation_fallback.get("trailing_pe"),
            "forward_pe": (snapshot or {}).get("forward_pe") or valuation_fallback.get("forward_pe"),
            "source": source_label,
            "fallback": (not intraday_rows) or "fallback" in source,
            "data_warning": "Yahoo quote/chart data can be delayed; live 5m falls back to 5d/1d when intraday bars are unavailable.",
        }
        base.QUOTE_CACHE[cache_key] = (now, payload)
        return payload
    raise HTTPException(status_code=404, detail=f"No quote data found for {', '.join(tried) or raw}.")


def improve_valuation_with_quote(symbol: str, market: str, valuation: dict[str, Any]) -> dict[str, Any]:
    quote = fetch_yahoo_quote_snapshot(symbol)
    live_price = quote.get("price") or valuation.get("price")
    currency = quote.get("currency") or valuation.get("currency") or ("TWD" if market == "TW" else "USD")
    cap = base.optional_number(quote.get("market_cap"), 0)
    shares = base.optional_number(quote.get("shares_outstanding"), 0)
    cap_source = "Yahoo quote live" if cap else None
    fallback: dict[str, Any] = {}
    if not cap:
        cap, fallback_currency, fallback_shares, cap_source, fallback = market_cap_fallback(symbol, market, live_price, currency)
        currency = fallback_currency or currency
        shares = shares or fallback_shares
    if not cap:
        return valuation
    pct = quote.get("change_pct")
    cap = int(cap)
    valuation = dict(valuation)
    valuation.update({
        "market_cap": cap,
        "currency": currency,
        "market_cap_previous": int(cap / (1 + pct / 100)) if pct is not None and pct > -99 else None,
        "market_cap_change_pct": base.number(pct) if pct is not None else None,
        "shares_outstanding": shares,
        "trailing_pe": quote.get("trailing_pe") or fallback.get("trailing_pe") or valuation.get("trailing_pe"),
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
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse

import app as base

app = base.app
BASE_DIR = Path(__file__).resolve().parent
ALERT_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}


def remove_route(path: str, methods: set[str] | None = None) -> None:
    methods = methods or {"GET"}
    app.router.routes = [
        route for route in app.router.routes
        if not (getattr(route, "path", "") == path and methods.intersection(set(getattr(route, "methods", []) or [])))
    ]


for route_path in {"/", "/api/analyze", "/api/quote/{symbol}", "/api/movers"}:
    remove_route(route_path)


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    html = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    script = '<script src="/static/openkiri-live.js?v=20260709"></script>'
    if script not in html:
        html = html.replace("</body>", f"{script}\n</body>")
    return HTMLResponse(html)


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
    news = base.fetch_news(resolved, market)
    event_context = base.build_event_context(resolved, market, news)
    risk = base.build_risk(latest, levels, news, event_context)
    suitability = base.build_suitability(latest, risk, news)
    prediction = base.build_prediction(rows, latest, risk, news, event_context)
    macro = base.fetch_macro_snapshot()
    visible_rows = rows[-180:]
    chart_math = base.build_chart_math(visible_rows, latest, risk, prediction)
    design_signals = base.build_design_signals(resolved, market, rows, latest, levels, risk, prediction, news, macro)
    universe_item = base.find_universe_item(resolved) or {"symbol": resolved, "name": resolved, "market": market, "industry": "Unknown", "market_cap_usd": 0}
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
    for item in base.STOCK_UNIVERSE:
        if item["market"] not in selected:
            continue
        row = base.mover_quote(item, mode=mode)
        if row:
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
    for market in sorted(selected):
        for item in [row for row in base.STOCK_UNIVERSE if row["market"] == market][:36]:
            signal = stock_alert_signal(item)
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
        variants.extend([("5d", "5m"), ("1mo", "15m"), ("5d", "1d")])
    elif interval == "15m":
        variants.extend([("5d", "15m"), ("1mo", "15m"), ("5d", "1d")])
    elif interval == "1h":
        variants.extend([("1mo", "1h"), ("5d", "1d")])
    elif period == "1d":
        variants.append(("5d", "1d"))
    out: list[tuple[str, str]] = []
    for item in variants:
        if item not in out:
            out.append(item)
    return out


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
        live_change = base.optional_number(snapshot.get("change")) if snapshot else None
        live_change_pct = base.optional_number(snapshot.get("change_pct")) if snapshot else None
        live_market_cap = base.optional_number(snapshot.get("market_cap"), 0) if snapshot else None
        shares = base.optional_number(snapshot.get("shares_outstanding"), 0) if snapshot else None
        if not live_market_cap and shares and live_price:
            live_market_cap = shares * live_price
        if live_price:
            latest["close"] = live_price
        change = live_change if live_change is not None else latest["close"] - previous_close if previous_close else 0
        change_pct = live_change_pct if live_change_pct is not None else (latest["close"] / previous_close - 1) * 100 if previous_close else 0
        avg_volume = base.mean([row["volume"] for row in rows[-20:-1] if row.get("volume") is not None]) if len(rows) >= 2 else 0
        volume_ratio = latest["volume"] / avg_volume if avg_volume else 1
        cap_previous = int(live_market_cap / (1 + change_pct / 100)) if live_market_cap and change_pct > -99 else None
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
            "currency": snapshot.get("currency") if snapshot else ("TWD" if market == "TW" else "USD"),
            "market_cap": int(live_market_cap) if live_market_cap else None,
            "market_cap_previous": cap_previous,
            "market_cap_change_pct": base.number(change_pct),
            "source": "Yahoo quote live + " + (source if intraday_rows else "5d/1d fallback") if snapshot else source if intraday_rows else "5d/1d fallback",
            "fallback": (not intraday_rows) or "fallback" in source,
            "data_warning": "Yahoo quote/chart data can be delayed; live 5m falls back to 5d/1d when intraday bars are unavailable.",
        }
        base.QUOTE_CACHE[cache_key] = (now, payload)
        return payload
    raise HTTPException(status_code=404, detail=f"No quote data found for {', '.join(tried) or raw}.")


def improve_valuation_with_quote(symbol: str, market: str, valuation: dict[str, Any]) -> dict[str, Any]:
    quote = fetch_yahoo_quote_snapshot(symbol)
    if not quote.get("market_cap"):
        return valuation
    pct = quote.get("change_pct")
    cap = int(quote["market_cap"])
    valuation = dict(valuation)
    valuation.update({
        "market_cap": cap,
        "currency": quote.get("currency") or valuation.get("currency") or ("TWD" if market == "TW" else "USD"),
        "market_cap_previous": int(cap / (1 + pct / 100)) if pct is not None and pct > -99 else None,
        "market_cap_change_pct": base.number(pct) if pct is not None else None,
        "shares_outstanding": quote.get("shares_outstanding"),
        "trailing_pe": quote.get("trailing_pe") or valuation.get("trailing_pe"),
        "forward_pe": quote.get("forward_pe") or valuation.get("forward_pe"),
        "source": "Yahoo quote live",
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
