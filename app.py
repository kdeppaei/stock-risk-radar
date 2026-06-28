from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import numpy as np
import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "stock_risk_log.sqlite3"

app = FastAPI(title="Stock Risk Radar", version="4.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

POSITIVE_WORDS = [
    "beat", "upgrade", "growth", "profit", "record", "surge", "rally", "bullish",
    "strong demand", "buy rating", "target raised", "outperform", "ai",
    "利多", "成長", "獲利", "上修", "看好", "買進", "突破", "創高", "接單", "營收", "法說", "目標價上調",
]
NEGATIVE_WORDS = [
    "miss", "downgrade", "loss", "drop", "plunge", "bearish", "weak demand",
    "sell rating", "target cut", "underperform", "lawsuit", "probe", "restriction",
    "利空", "衰退", "虧損", "下修", "賣出", "跌破", "賣壓", "庫存", "調查", "限制", "目標價下調",
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
    raw_symbol = symbol.strip().upper()
    normalized, market = normalize_symbol(raw_symbol)
    candidates = candidate_symbols(normalized, raw_symbol)
    for candidate in candidates:
        intraday = fetch_price_history(candidate, "1d", "5m")
        if intraday.empty:
            intraday = fetch_price_history(candidate, "5d", "1d")
        if not intraday.empty and len(intraday) >= 2:
            data = normalize_columns(intraday)
            latest = data.iloc[-1]
            previous = data.iloc[-2]
            return {
                "ok": True,
                "symbol": candidate,
                "market": market,
                "date": data.index[-1].strftime("%Y-%m-%d %H:%M"),
                "price": number(latest["Close"]),
                "change": number(latest["Close"] - previous["Close"]),
                "change_pct": number((latest["Close"] / previous["Close"] - 1) * 100) if previous["Close"] else 0,
                "source": "1d/5m" if len(intraday) > 10 else "5d/1d",
            }
    raise HTTPException(status_code=404, detail="No quote data found.")


@app.get("/api/analyze")
def analyze(
    symbol: str = Query(..., min_length=1, max_length=32),
    period: str = Query("1y", pattern="^(1d|5d|1mo|6mo|1y|2y|5y)$"),
    interval: str = Query("1d", pattern="^(5m|15m|1h|1d|1wk)$"),
) -> dict[str, Any]:
    period, interval = normalize_period_interval(period, interval)
    raw_symbol = symbol.strip().upper()
    normalized, market = normalize_symbol(raw_symbol)
    candidates = candidate_symbols(normalized, raw_symbol)

    hist = pd.DataFrame()
    resolved = normalized
    for candidate in candidates:
        hist = fetch_price_history(candidate, period, interval)
        if not hist.empty:
            resolved = candidate
            break

    if hist.empty:
        raise HTTPException(status_code=404, detail="No price data found. Please check the ticker symbol.")

    data = calculate_indicators(normalize_columns(hist))
    if len(data) < 20:
        raise HTTPException(status_code=422, detail="Not enough data to calculate indicators.")

    latest = data.iloc[-1]
    previous = data.iloc[-2]
    levels = support_resistance(data)
    news = fetch_news(resolved, market)
    risk = build_risk(latest, levels, news)
    suitability = build_suitability(latest, risk, news)
    prediction = build_prediction(data, latest, risk, news)
    macro = fetch_macro_snapshot()

    response = {
        "ok": True,
        "symbol": resolved,
        "input_symbol": raw_symbol,
        "market": market,
        "period": period,
        "interval": interval,
        "latest": {
            "date": data.index[-1].strftime("%Y-%m-%d %H:%M") if interval in {"5m", "15m", "1h"} else data.index[-1].strftime("%Y-%m-%d"),
            "open": number(latest["Open"]),
            "high": number(latest["High"]),
            "low": number(latest["Low"]),
            "close": number(latest["Close"]),
            "volume": int(latest["Volume"]) if not pd.isna(latest["Volume"]) else 0,
        },
        "change": {
            "amount": number(latest["Close"] - previous["Close"]),
            "pct": number((latest["Close"] / previous["Close"] - 1) * 100) if previous["Close"] else 0,
        },
        "technical": technical_payload(latest),
        "levels": levels,
        "risk": risk,
        "suitability": suitability,
        "prediction": prediction,
        "news": news,
        "macro": macro,
        "chart": build_chart_rows(data.tail(180), market),
    }
    save_log(raw_symbol, resolved, market, response)
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


def fetch_price_history(symbol: str, period: str, interval: str) -> pd.DataFrame:
    # Render Free is much more stable when we avoid importing/running yfinance.
    # Yahoo chart API returns the OHLCV data this app needs with less memory pressure.
    return fetch_yahoo_chart(symbol, period, interval)


def fetch_yahoo_chart(symbol: str, period: str, interval: str) -> pd.DataFrame:
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": period, "interval": interval},
            headers={"User-Agent": "Mozilla/5.0 StockRiskRadar/4.3"},
            timeout=20,
        )
        resp.raise_for_status()
        result = (resp.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return pd.DataFrame()
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        if not timestamps or not quote:
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "Open": quote.get("open"),
                "High": quote.get("high"),
                "Low": quote.get("low"),
                "Close": quote.get("close"),
                "Volume": quote.get("volume"),
            },
            index=pd.to_datetime(timestamps, unit="s").tz_localize("UTC").tz_convert(None),
        ).dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


def quote_last(symbol: str) -> dict[str, Any]:
    data = fetch_price_history(symbol, "5d", "1d")
    if data.empty or len(data) < 2:
        return {"symbol": symbol, "price": None, "change_pct": None}
    data = normalize_columns(data)
    last = data.iloc[-1]["Close"]
    prev = data.iloc[-2]["Close"]
    return {"symbol": symbol, "price": number(last, 4), "change_pct": number((last / prev - 1) * 100)}


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


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [col[0] for col in out.columns]
    out = out.rename(columns=str.title)
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [col for col in required if col not in out.columns]
    if missing:
        raise HTTPException(status_code=502, detail=f"Price data missing columns: {', '.join(missing)}")
    return out[required].dropna(subset=["Open", "High", "Low", "Close"])


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.dropna(subset=["Open", "High", "Low", "Close"])
    data["MA5"] = data["Close"].rolling(5, min_periods=1).mean()
    data["MA20"] = data["Close"].rolling(20, min_periods=1).mean()
    data["MA60"] = data["Close"].rolling(60, min_periods=1).mean()
    ema12 = data["Close"].ewm(span=12, adjust=False).mean()
    ema26 = data["Close"].ewm(span=26, adjust=False).mean()
    data["MACD"] = ema12 - ema26
    data["MACD_SIGNAL"] = data["MACD"].ewm(span=9, adjust=False).mean()
    delta = data["Close"].diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    data["RSI14"] = (100 - 100 / (1 + rs)).fillna(50)
    prev_close = data["Close"].shift(1)
    tr = pd.concat([data["High"] - data["Low"], (data["High"] - prev_close).abs(), (data["Low"] - prev_close).abs()], axis=1).max(axis=1)
    data["ATR14"] = tr.rolling(14, min_periods=1).mean()
    data["VOLUME_RATIO"] = (data["Volume"] / data["Volume"].rolling(20, min_periods=1).mean()).fillna(1)
    data["RET1"] = data["Close"].pct_change(1).fillna(0)
    data["RET5"] = data["Close"].pct_change(5).fillna(0)
    data["RET20"] = data["Close"].pct_change(20).fillna(0)
    return data


def technical_payload(latest: pd.Series) -> dict[str, float]:
    return {
        "ma5": number(latest["MA5"]),
        "ma20": number(latest["MA20"]),
        "ma60": number(latest["MA60"]),
        "rsi14": number(latest["RSI14"]),
        "macd": number(latest["MACD"]),
        "macd_signal": number(latest["MACD_SIGNAL"]),
        "atr14": number(latest["ATR14"]),
        "atr_pct": number(latest["ATR14"] / latest["Close"] * 100) if latest["Close"] else 0,
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
    label = "positive" if total >= 2 else "negative" if total <= -2 else "neutral"
    return {
        "query": query,
        "label": label,
        "score": total,
        "positive": sum(1 for item in items if int(item["sentiment"]) > 0),
        "negative": sum(1 for item in items if int(item["sentiment"]) < 0),
        "items": items[:10],
    }


def fetch_feed(url: str, source: str) -> list[dict[str, Any]]:
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 StockRiskRadar/4.3"}, timeout=10)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        rows = []
        for entry in feed.entries[:10]:
            title = getattr(entry, "title", "") or ""
            summary = getattr(entry, "summary", "") or ""
            if title:
                rows.append(
                    {
                        "source": source,
                        "title": title,
                        "link": getattr(entry, "link", "") or "",
                        "published": getattr(entry, "published", "") or getattr(entry, "updated", "") or "",
                        "sentiment": score_sentiment(f"{title} {summary}"),
                    }
                )
        return rows
    except Exception as exc:
        return [{"source": source, "title": f"News fetch failed: {exc}", "link": "", "published": "", "sentiment": 0}]


def score_sentiment(text: str) -> int:
    lower = text.lower()
    score = sum(1 for word in POSITIVE_WORDS if word.lower() in lower)
    score -= sum(1 for word in NEGATIVE_WORDS if word.lower() in lower)
    return max(-3, min(3, score))


def support_resistance(data: pd.DataFrame) -> dict[str, float]:
    recent = data.tail(min(60, len(data)))
    latest = data.iloc[-1]
    support = float(recent["Low"].min())
    resistance = float(recent["High"].max())
    atr = float(latest["ATR14"] or 0)
    return {
        "support_60d": number(support),
        "resistance_60d": number(resistance),
        "breakout_call": number(resistance * 1.01),
        "backtest_zone": number(max(support, latest["Close"] - atr)),
        "stop_loss_reference": number(max(0, support - atr * 0.5)),
    }


def build_risk(latest: pd.Series, levels: dict[str, float], news: dict[str, Any]) -> dict[str, Any]:
    close = float(latest["Close"])
    ma20 = float(latest["MA20"])
    ma60 = float(latest["MA60"])
    rsi = float(latest["RSI14"] or 50)
    macd = float(latest["MACD"] or 0)
    macd_signal = float(latest["MACD_SIGNAL"] or 0)
    volume_ratio = float(latest["VOLUME_RATIO"] or 1)
    ret20 = float(latest["RET20"] or 0) * 100
    score = 45
    reasons = []
    if close > ma20 > ma60:
        score -= 10
        reasons.append("Price is above MA20 and MA60.")
    elif close < ma20 < ma60:
        score += 15
        reasons.append("Price is below MA20 and MA60.")
    if rsi >= 75:
        score += 12
        reasons.append("RSI is overheated.")
    elif rsi <= 30:
        score += 8
        reasons.append("RSI is weak or oversold.")
    elif 45 <= rsi <= 65:
        score -= 5
        reasons.append("RSI is balanced.")
    if macd > macd_signal:
        score -= 5
        reasons.append("MACD is bullish.")
    else:
        score += 5
        reasons.append("MACD is weak.")
    if volume_ratio >= 1.8:
        score += 8
        reasons.append("Volume is unusually high.")
    if ret20 > 10:
        score += 6
        reasons.append("20-period return is extended.")
    elif ret20 < -10:
        score += 10
        reasons.append("20-period return is deeply negative.")
    if news["label"] == "positive":
        score -= 5
        reasons.append("News keywords lean positive.")
    elif news["label"] == "negative":
        score += 8
        reasons.append("News keywords lean negative.")
    score = int(max(0, min(100, score)))
    trend = "bullish" if close > ma20 and macd > macd_signal else "bearish" if close < ma20 else "sideways"
    level = "low" if score < 40 else "medium" if score < 70 else "high"
    summary = f"{trend.title()} bias with risk {score}/100. Support {levels['support_60d']}, resistance {levels['resistance_60d']}."
    return {"score": score, "level": level, "trend": trend, "summary": summary, "reasons": reasons}


def build_suitability(latest: pd.Series, risk: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    close = float(latest["Close"])
    atr_pct = float(latest["ATR14"] / close * 100) if close else 0
    volume_ratio = float(latest["VOLUME_RATIO"] or 1)
    ret5 = float(latest["RET5"] or 0) * 100
    ret20 = float(latest["RET20"] or 0) * 100
    risk_score = int(risk["score"])
    intraday_score = 55 + min(18, volume_ratio * 8) + min(10, atr_pct) - risk_score / 4
    short_score = 58 + ret5 * 0.8 - risk_score / 5
    long_score = 64 + ret20 * 0.45 - risk_score / 4 - atr_pct
    if news["label"] == "negative":
        short_score -= 8
        long_score -= 8
    return {
        "intraday": suitability_label(intraday_score, "Volume, ATR and current risk are used."),
        "short_term": suitability_label(short_score, "5-period momentum, risk and news are used."),
        "long_term": suitability_label(long_score, "20-period trend, MA60, risk and volatility are used."),
    }


def suitability_label(score: float, reason: str) -> dict[str, Any]:
    label = "suitable" if score >= 70 else "watch" if score >= 50 else "avoid"
    return {"score": number(score), "label": label, "reason": reason}


def build_prediction(data: pd.DataFrame, latest: pd.Series, risk: dict[str, Any], news: dict[str, Any]) -> dict[str, Any]:
    recent = data.dropna(subset=["Close"]).tail(min(60, len(data)))
    closes = recent["Close"].astype(float).to_numpy()
    if len(closes) < 20:
        return {"model": "OLS trend + momentum", "bias": "neutral", "confidence": 0, "forecast_5d_pct": 0, "forecast_20d_pct": 0, "advice": "Not enough data.", "drivers": []}
    x = np.arange(len(closes), dtype=float)
    slope, intercept = np.polyfit(x, closes, 1)
    fitted = slope * x + intercept
    den = float(np.sum((closes - closes.mean()) ** 2))
    r2 = 0 if den == 0 else 1 - float(np.sum((closes - fitted) ** 2)) / den
    last_close = float(closes[-1])
    forecast_5 = float((slope * (len(closes) + 4) + intercept) / last_close - 1) * 100
    forecast_20 = float((slope * (len(closes) + 19) + intercept) / last_close - 1) * 100
    m5 = float(latest["RET5"] or 0) * 100
    m20 = float(latest["RET20"] or 0) * 100
    macd_edge = float(latest["MACD"] or 0) - float(latest["MACD_SIGNAL"] or 0)
    news_edge = 1 if news["label"] == "positive" else -1 if news["label"] == "negative" else 0
    composite = forecast_20 * 0.45 + m20 * 0.25 + m5 * 0.15 + (5 if macd_edge > 0 else -5) * 0.1 + news_edge * 4
    bias = "bullish" if composite >= 4 else "bearish" if composite <= -4 else "neutral"
    confidence = int(max(5, min(92, abs(composite) * 8 + max(0, r2) * 35 - int(risk["score"]) * 0.15)))
    advice = {
        "bullish": "Prefer pullback entries. Long-term view is constructive if price holds key moving averages.",
        "bearish": "Avoid chasing. Wait for price to reclaim MA20 or for selling pressure to fade.",
        "neutral": "Range-bound setup. Use support/resistance instead of directional conviction.",
    }[bias]
    drivers = [
        f"OLS 20-step forecast {number(forecast_20)}%.",
        f"Momentum: 5-period {number(m5)}%, 20-period {number(m20)}%.",
        "MACD above signal." if macd_edge > 0 else "MACD below signal.",
        f"News sentiment: {news['label']}.",
    ]
    return {"model": "OLS trend + momentum", "bias": bias, "confidence": confidence, "forecast_5d_pct": number(forecast_5), "forecast_20d_pct": number(forecast_20), "advice": advice, "drivers": drivers}


def build_chart_rows(data: pd.DataFrame, market: str) -> list[dict[str, Any]]:
    rows = []
    for idx, row in data.iterrows():
        up = float(row["Close"]) >= float(row["Open"])
        rows.append(
            {
                "date": idx.strftime("%m-%d %H:%M") if idx.hour or idx.minute else idx.strftime("%Y-%m-%d"),
                "open": number(row["Open"]),
                "high": number(row["High"]),
                "low": number(row["Low"]),
                "close": number(row["Close"]),
                "volume": int(row["Volume"]) if not pd.isna(row["Volume"]) else 0,
                "ma5": number(row["MA5"]),
                "ma20": number(row["MA20"]),
                "ma60": number(row["MA60"]),
                "color": candle_color(up, market),
            }
        )
    return rows


def candle_color(up: bool, market: str) -> str:
    if market == "TW":
        return "#ef4444" if up else "#22c55e"
    return "#22c55e" if up else "#ef4444"


def save_log(input_symbol: str, symbol: str, market: str, response: dict[str, Any]) -> None:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO analysis_log
            (created_at, input_symbol, normalized_symbol, market, close_price, risk_score, trend_label, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                input_symbol,
                symbol,
                market,
                response["latest"]["close"],
                response["risk"]["score"],
                response["risk"]["trend"],
                json.dumps(response, ensure_ascii=False),
            ),
        )


def number(value: Any, digits: int = 2) -> float:
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return 0
        return round(value, digits)
    except Exception:
        return 0
