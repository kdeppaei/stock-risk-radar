from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "stock_risk_log.sqlite3"

app = FastAPI(title="Stock Risk Radar", version="4.4.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

POSITIVE_WORDS = [
    "beat", "upgrade", "growth", "profit", "record", "surge", "rally", "bullish", "strong demand",
    "buy rating", "target raised", "outperform", "ai", "利多", "成長", "獲利", "上修", "看好", "買進",
    "突破", "創高", "接單", "營收", "法說", "目標價上調",
]
NEGATIVE_WORDS = [
    "miss", "downgrade", "loss", "drop", "plunge", "bearish", "weak demand", "sell rating",
    "target cut", "underperform", "lawsuit", "probe", "restriction", "利空", "衰退", "虧損", "下修",
    "賣出", "跌破", "賣壓", "庫存", "調查", "限制", "目標價下調",
]


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                input_symbol TEXT NOT NULL,
                normalized_symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                close_price REAL,
                risk_score INTEGER,
                trend_label TEXT,
                raw_json TEXT
            )
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/market-context")
def market_context() -> dict[str, Any]:
    usd_twd = quote_last("TWD=X")
    macro = fetch_macro_snapshot()
    return {
        "taipei_time": datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S"),
        "new_york_time": datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M:%S"),
        "usd_twd": usd_twd,
        "macro": macro,
    }


@app.get("/api/quote")
def quote(symbol: str = Query(..., min_length=1, max_length=32)) -> dict[str, Any]:
    raw = symbol.strip().upper()
    normalized, market = normalize_symbol(raw)
    for candidate in candidate_symbols(normalized, raw):
        rows = fetch_price_history(candidate, "1d", "5m") or fetch_price_history(candidate, "5d", "1d")
        if len(rows) >= 2:
            latest, previous = rows[-1], rows[-2]
            return {
                "ok": True,
                "symbol": candidate,
                "market": market,
                "date": latest["date_label"],
                "price": number(latest["close"]),
                "change": number(latest["close"] - previous["close"]),
                "change_pct": number((latest["close"] / previous["close"] - 1) * 100) if previous["close"] else 0,
                "source": "1d/5m" if len(rows) > 10 else "5d/1d",
            }
    raise HTTPException(status_code=404, detail="No quote data found.")


@app.get("/api/analyze")
def analyze(
    symbol: str = Query(..., min_length=1, max_length=32),
    period: str = Query("1y", pattern="^(1d|5d|1mo|6mo|1y|2y|5y)$"),
    interval: str = Query("1d", pattern="^(5m|15m|1h|1d|1wk)$"),
) -> dict[str, Any]:
    period, interval = normalize_period_interval(period, interval)
    raw = symbol.strip().upper()
    normalized, market = normalize_symbol(raw)

    rows: list[dict[str, Any]] = []
    resolved = normalized
    for candidate in candidate_symbols(normalized, raw):
        rows = fetch_price_history(candidate, period, interval)
        if rows:
            resolved = candidate
            break
    if len(rows) < 20:
        raise HTTPException(status_code=422, detail="Not enough price data.")

    rows = calculate_indicators(rows)
    latest, previous = rows[-1], rows[-2]
    levels = support_resistance(rows)
    news = fetch_news(resolved, market)
    risk = build_risk(latest, levels, news)
    suitability = build_suitability(latest, risk, news)
    prediction = build_prediction(rows, latest, risk, news)
    macro = fetch_macro_snapshot()

    response = {
        "ok": True,
        "symbol": resolved,
        "input_symbol": raw,
        "market": market,
        "period": period,
        "interval": interval,
        "latest": {
            "date": latest["date_label"],
            "open": number(latest["open"]),
            "high": number(latest["high"]),
            "low": number(latest["low"]),
            "close": number(latest["close"]),
            "volume": int(latest["volume"] or 0),
        },
        "change": {
            "amount": number(latest["close"] - previous["close"]),
            "pct": number((latest["close"] / previous["close"] - 1) * 100) if previous["close"] else 0,
        },
        "technical": technical_payload(latest),
        "levels": levels,
        "risk": risk,
        "suitability": suitability,
        "prediction": prediction,
        "news": news,
        "macro": macro,
        "chart": build_chart_rows(rows[-180:], market),
    }
    save_log(raw, resolved, market, response)
    return response


def normalize_period_interval(period: str, interval: str) -> tuple[str, str]:
    if period == "1d":
        return "1d", "5m" if interval in {"1d", "1wk"} else interval
    if interval in {"5m", "15m", "1h"} and period not in {"1d", "5d", "1mo"}:
        return "5d", interval
    return period, interval


def normalize_symbol(text: str) -> tuple[str, str]:
    raw = text.strip().upper().replace(" ", "")
    if raw.endswith((".TW", ".TWO")):
        return raw, "TW"
    if raw.isdigit():
        return f"{raw}.TW", "TW"
    return raw, "US"


def candidate_symbols(normalized: str, raw: str) -> list[str]:
    if raw.isdigit() and len(raw) == 4:
        return [f"{raw}.TW", f"{raw}.TWO"]
    return [normalized]


def fetch_price_history(symbol: str, period: str, interval: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": period, "interval": interval},
            headers={"User-Agent": "Mozilla/5.0 StockRiskRadar/4.4"},
            timeout=15,
        )
        resp.raise_for_status()
        result = (resp.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return []
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        rows = []
        for i, ts in enumerate(timestamps):
            try:
                close = quote.get("close", [])[i]
                open_ = quote.get("open", [])[i]
                high = quote.get("high", [])[i]
                low = quote.get("low", [])[i]
                if close is None or open_ is None or high is None or low is None:
                    continue
                dt = datetime.fromtimestamp(ts, timezone.utc)
                rows.append(
                    {
                        "date": dt,
                        "date_label": dt.strftime("%Y-%m-%d %H:%M") if interval in {"5m", "15m", "1h"} else dt.strftime("%Y-%m-%d"),
                        "open": float(open_),
                        "high": float(high),
                        "low": float(low),
                        "close": float(close),
                        "volume": float((quote.get("volume") or [0])[i] or 0),
                    }
                )
            except (IndexError, TypeError, ValueError):
                continue
        return rows
    except Exception:
        return []


def quote_last(symbol: str) -> dict[str, Any]:
    rows = fetch_price_history(symbol, "5d", "1d")
    if len(rows) < 2:
        return {"symbol": symbol, "price": None, "change_pct": None}
    last, prev = rows[-1]["close"], rows[-2]["close"]
    return {"symbol": symbol, "price": number(last, 4), "change_pct": number((last / prev - 1) * 100) if prev else None}


def fetch_macro_snapshot() -> dict[str, Any]:
    symbols = {"sp500": "^GSPC", "nasdaq": "^IXIC", "sox": "^SOX", "us10y": "^TNX", "usd_index": "DX-Y.NYB"}
    rows = {key: quote_last(sym) for key, sym in symbols.items()}
    score = 0
    for key in ["sp500", "nasdaq", "sox"]:
        pct = rows[key]["change_pct"]
        if pct is not None:
            score += 1 if pct > 0 else -1
    if rows["us10y"]["change_pct"] is not None and rows["us10y"]["change_pct"] > 1:
        score -= 1
    label = "risk-on" if score >= 2 else "risk-off" if score <= -2 else "mixed"
    return {"label": label, "items": rows}


def calculate_indicators(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes: list[float] = []
    macd_values: list[float] = []
    ema12 = ema26 = signal = None
    for i, row in enumerate(rows):
        close = row["close"]
        closes.append(close)
        row["MA5"] = avg(closes[-5:])
        row["MA20"] = avg(closes[-20:])
        row["MA60"] = avg(closes[-60:])
        ema12 = ema_next(close, ema12, 12)
        ema26 = ema_next(close, ema26, 26)
        row["MACD"] = ema12 - ema26
        macd_values.append(row["MACD"])
        signal = ema_next(row["MACD"], signal, 9)
        row["MACD_SIGNAL"] = signal
        deltas = [closes[j] - closes[j - 1] for j in range(max(1, len(closes) - 14), len(closes))]
        gains = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]
        avg_gain = avg(gains) if gains else 0
        avg_loss = avg(losses) if losses else 0
        row["RSI14"] = 100 if avg_loss == 0 and avg_gain > 0 else 50 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
        prev_close = rows[i - 1]["close"] if i else close
        tr = max(row["high"] - row["low"], abs(row["high"] - prev_close), abs(row["low"] - prev_close))
        trs = [max(rows[j]["high"] - rows[j]["low"], abs(rows[j]["high"] - (rows[j - 1]["close"] if j else rows[j]["close"])), abs(rows[j]["low"] - (rows[j - 1]["close"] if j else rows[j]["close"]))) for j in range(max(0, i - 13), i + 1)]
        row["ATR14"] = avg(trs) if trs else tr
        vols = [r["volume"] for r in rows[max(0, i - 19): i + 1]]
        row["VOLUME_RATIO"] = row["volume"] / avg(vols) if avg(vols) else 1
        row["RET1"] = pct_change(closes, 1)
        row["RET5"] = pct_change(closes, 5)
        row["RET20"] = pct_change(closes, 20)
    return rows


def technical_payload(latest: dict[str, Any]) -> dict[str, float]:
    return {
        "ma5": number(latest["MA5"]),
        "ma20": number(latest["MA20"]),
        "ma60": number(latest["MA60"]),
        "rsi14": number(latest["RSI14"]),
        "macd": number(latest["MACD"]),
        "macd_signal": number(latest["MACD_SIGNAL"]),
        "atr14": number(latest["ATR14"]),
        "atr_pct": number(latest["ATR14"] / latest["close"] * 100) if latest["close"] else 0,
        "volume_ratio": number(latest["VOLUME_RATIO"]),
        "ret1_pct": number(latest["RET1"] * 100),
        "ret5_pct": number(latest["RET5"] * 100),
        "ret20_pct": number(latest["RET20"] * 100),
    }


def fetch_news(symbol: str, market: str) -> dict[str, Any]:
    plain = symbol.replace(".TW", "").replace(".TWO", "")
    if market == "TW":
        query = f"{plain} 股票 營收 法說 股價"
        urls = [(f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant", "Google News")]
    else:
        query = f"{symbol} stock earnings analyst rating"
        urls = [
            (f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote_plus(symbol)}&region=US&lang=en-US", "Yahoo Finance"),
            (f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en", "Google News"),
        ]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url, source in urls:
        for item in fetch_feed(url, source):
            key = item["title"].strip().lower()
            if key and key not in seen:
                seen.add(key)
                items.append(item)
    total = sum(int(item["sentiment"]) for item in items)
    return {
        "query": query,
        "label": "positive" if total >= 2 else "negative" if total <= -2 else "neutral",
        "score": total,
        "positive": sum(1 for item in items if int(item["sentiment"]) > 0),
        "negative": sum(1 for item in items if int(item["sentiment"]) < 0),
        "items": items[:8],
    }


def fetch_feed(url: str, source: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 StockRiskRadar/4.4"}, timeout=8)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        rows = []
        for entry in feed.entries[:8]:
            title = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            if title:
                rows.append({"source": source, "title": title, "link": getattr(entry, "link", "") or "", "published": getattr(entry, "published", "") or getattr(entry, "updated", "") or "", "sentiment": score_sentiment(f"{title} {summary}")})
        return rows
    except Exception as exc:
        return [{"source": source, "title": f"News fetch failed: {exc}", "link": "", "published": "", "sentiment": 0}]


def score_sentiment(text: str) -> int:
    lower = text.lower()
    score = sum(1 for word in POSITIVE_WORDS if word.lower() in lower)
    score -= sum(1 for word in NEGATIVE_WORDS if word.lower() in lower)
    return max(-3, min(3, score))


def support_resistance(rows: list[dict[str, Any]]) -> dict[str, float]:
    recent = rows[-min(60, len(rows)):]
    latest = rows[-1]
    support = min(row["low"] for row in recent)
    resistance = max(row["high"] for row in recent)
    atr = latest["ATR14"] or 0
    return {"support_60d": number(support), "resistance_60d": number(resistance), "breakout_call": number(resistance * 1.01), "backtest_zone": number(max(support, latest["close"] - atr)), "stop_loss_reference": number(max(0, support - atr * 0.5))}


def build_risk(latest: dict[str, Any], levels: dict[str, float], news: dict[str, Any]) -> dict[str, Any]:
    close, ma20, ma60 = latest["close"], latest["MA20"], latest["MA60"]
    rsi, macd, macd_signal = latest["RSI14"], latest["MACD"], latest["MACD_SIGNAL"]
    score = 45
    reasons = []
    if close > ma20 > ma60:
        score -= 10; reasons.append("Price is above MA20 and MA60.")
    elif close < ma20 < ma60:
        score += 15; reasons.append("Price is below MA20 and MA60.")
    if rsi >= 75:
        score += 12; reasons.append("RSI is overheated.")
    elif rsi <= 30:
        score += 8; reasons.append("RSI is weak or oversold.")
    elif 45 <= rsi <= 65:
        score -= 5; reasons.append("RSI is balanced.")
    if macd > macd_signal:
        score -= 5; reasons.append("MACD is bullish.")
    else:
        score += 5; reasons.append("MACD is weak.")
    if latest["VOLUME_RATIO"] >= 1.8:
        score += 8; reasons.append("Volume is unusually high.")
    ret20 = latest["RET20"] * 100
    if ret20 > 10:
        score += 6; reasons.append("20-period return is extended.")
    elif ret20 < -10:
        score += 10; reasons.append("20-period return is deeply negative.")
    if news["label"] == "positive":
        score -= 5; reasons.append("News keywords lean positive.")
    elif news["label"] == "negative":
        score += 8; reasons.append("News keywords lean negative.")
    score = int(max(0, min(100, score)))
    trend = "bullish" if close > ma20 and macd > macd_signal else "bearish" if close < ma20 else "sideways"
    return {"score": score, "level": "low" if score < 40 else "medium" if score < 70 else "high", "trend": trend, "summary": f"{trend.title()} bias with risk {score}/100. Support {levels['support_60d']}, resistance {levels['resistance_60d']}.", "reasons": reasons}


def build_suitability(latest: dict[str, Any], risk: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    close = latest["close"]
    atr_pct = latest["ATR14"] / close * 100 if close else 0
    risk_score = int(risk["score"])
    intraday = 55 + min(18, latest["VOLUME_RATIO"] * 8) + min(10, atr_pct) - risk_score / 4
    short = 58 + latest["RET5"] * 100 * 0.8 - risk_score / 5
    long = 64 + latest["RET20"] * 100 * 0.45 - risk_score / 4 - atr_pct
    if news["label"] == "negative":
        short -= 8; long -= 8
    return {"intraday": suitability_label(intraday), "short_term": suitability_label(short), "long_term": suitability_label(long)}


def suitability_label(score: float) -> dict[str, Any]:
    return {"score": number(score), "label": "suitable" if score >= 70 else "watch" if score >= 50 else "avoid"}


def build_prediction(rows: list[dict[str, Any]], latest: dict[str, Any], risk: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    closes = [row["close"] for row in rows[-min(60, len(rows)):]]
    if len(closes) < 20:
        return {"model": "OLS trend + momentum", "bias": "neutral", "confidence": 0, "forecast_5d_pct": 0, "forecast_20d_pct": 0, "advice": "Not enough data.", "drivers": []}
    slope, intercept, r2 = linear_regression(closes)
    last_close = closes[-1]
    forecast_5 = ((slope * (len(closes) + 4) + intercept) / last_close - 1) * 100
    forecast_20 = ((slope * (len(closes) + 19) + intercept) / last_close - 1) * 100
    m5, m20 = latest["RET5"] * 100, latest["RET20"] * 100
    macd_edge = latest["MACD"] - latest["MACD_SIGNAL"]
    news_edge = 1 if news["label"] == "positive" else -1 if news["label"] == "negative" else 0
    composite = forecast_20 * 0.45 + m20 * 0.25 + m5 * 0.15 + (5 if macd_edge > 0 else -5) * 0.1 + news_edge * 4
    bias = "bullish" if composite >= 4 else "bearish" if composite <= -4 else "neutral"
    confidence = int(max(5, min(92, abs(composite) * 8 + max(0, r2) * 35 - int(risk["score"]) * 0.15)))
    advice = {"bullish": "Prefer pullback entries. Long-term view is constructive if price holds key moving averages.", "bearish": "Avoid chasing. Wait for price to reclaim MA20 or for selling pressure to fade.", "neutral": "Range-bound setup. Use support/resistance instead of directional conviction."}[bias]
    return {"model": "OLS trend + momentum", "bias": bias, "confidence": confidence, "forecast_5d_pct": number(forecast_5), "forecast_20d_pct": number(forecast_20), "advice": advice, "drivers": [f"OLS 20-step forecast {number(forecast_20)}%.", f"Momentum: 5-period {number(m5)}%, 20-period {number(m20)}%.", "MACD above signal." if macd_edge > 0 else "MACD below signal.", f"News sentiment: {news['label']}."]}


def build_chart_rows(rows: list[dict[str, Any]], market: str) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        up = row["close"] >= row["open"]
        out.append({"date": row["date_label"], "open": number(row["open"]), "high": number(row["high"]), "low": number(row["low"]), "close": number(row["close"]), "volume": int(row["volume"] or 0), "ma5": number(row["MA5"]), "ma20": number(row["MA20"]), "ma60": number(row["MA60"]), "color": candle_color(up, market)})
    return out


def candle_color(up: bool, market: str) -> str:
    return "#ef4444" if market == "TW" and up else "#22c55e" if market == "TW" else "#22c55e" if up else "#ef4444"


def save_log(input_symbol: str, symbol: str, market: str, response: dict[str, Any]) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO analysis_log (created_at, input_symbol, normalized_symbol, market, close_price, risk_score, trend_label, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), input_symbol, symbol, market, response["latest"]["close"], response["risk"]["score"], response["risk"]["trend"], json.dumps(response, ensure_ascii=False)),
        )


def ema_next(value: float, previous: float | None, span: int) -> float:
    if previous is None:
        return value
    k = 2 / (span + 1)
    return value * k + previous * (1 - k)


def linear_regression(values: list[float]) -> tuple[float, float, float]:
    n = len(values)
    xs = list(range(n))
    x_mean, y_mean = mean(xs), mean(values)
    den = sum((x - x_mean) ** 2 for x in xs)
    slope = 0 if den == 0 else sum((xs[i] - x_mean) * (values[i] - y_mean) for i in range(n)) / den
    intercept = y_mean - slope * x_mean
    ss_tot = sum((y - y_mean) ** 2 for y in values)
    ss_res = sum((values[i] - (slope * xs[i] + intercept)) ** 2 for i in range(n))
    r2 = 0 if ss_tot == 0 else 1 - ss_res / ss_tot
    return slope, intercept, r2


def pct_change(values: list[float], periods: int) -> float:
    if len(values) <= periods or not values[-periods - 1]:
        return 0
    return values[-1] / values[-periods - 1] - 1


def avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0


def number(value: Any, digits: int = 2) -> float:
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return 0
        return round(value, digits)
    except Exception:
        return 0
