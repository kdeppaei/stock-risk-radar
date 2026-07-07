from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app = FastAPI(title="OpenKiri", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

HEADERS = {"User-Agent": "OpenKiri/1.0 (+https://github.com/kdeppaei/stock-risk-radar)"}
CHART_CACHE: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
VALUATION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

STOCK_UNIVERSE = [
    {"symbol": "NVDA", "name": "NVIDIA", "market": "US", "industry": "Semiconductors", "market_cap_usd": 3_800_000_000_000},
    {"symbol": "AAPL", "name": "Apple", "market": "US", "industry": "Technology", "market_cap_usd": 3_200_000_000_000},
    {"symbol": "MSFT", "name": "Microsoft", "market": "US", "industry": "Technology", "market_cap_usd": 3_100_000_000_000},
    {"symbol": "GOOGL", "name": "Alphabet", "market": "US", "industry": "Communication", "market_cap_usd": 2_300_000_000_000},
    {"symbol": "AMZN", "name": "Amazon", "market": "US", "industry": "Consumer", "market_cap_usd": 2_200_000_000_000},
    {"symbol": "META", "name": "Meta Platforms", "market": "US", "industry": "Communication", "market_cap_usd": 1_700_000_000_000},
    {"symbol": "AVGO", "name": "Broadcom", "market": "US", "industry": "Semiconductors", "market_cap_usd": 1_500_000_000_000},
    {"symbol": "TSLA", "name": "Tesla", "market": "US", "industry": "Automotive", "market_cap_usd": 1_100_000_000_000},
    {"symbol": "BRK-B", "name": "Berkshire Hathaway", "market": "US", "industry": "Financials", "market_cap_usd": 1_000_000_000_000},
    {"symbol": "JPM", "name": "JPMorgan Chase", "market": "US", "industry": "Financials", "market_cap_usd": 750_000_000_000},
    {"symbol": "LLY", "name": "Eli Lilly", "market": "US", "industry": "Healthcare", "market_cap_usd": 720_000_000_000},
    {"symbol": "WMT", "name": "Walmart", "market": "US", "industry": "Retail", "market_cap_usd": 800_000_000_000},
    {"symbol": "ORCL", "name": "Oracle", "market": "US", "industry": "Technology", "market_cap_usd": 500_000_000_000},
    {"symbol": "NFLX", "name": "Netflix", "market": "US", "industry": "Communication", "market_cap_usd": 450_000_000_000},
    {"symbol": "AMD", "name": "Advanced Micro Devices", "market": "US", "industry": "Semiconductors", "market_cap_usd": 330_000_000_000},
    {"symbol": "MU", "name": "Micron Technology", "market": "US", "industry": "Semiconductors", "market_cap_usd": 170_000_000_000},
    {"symbol": "QCOM", "name": "Qualcomm", "market": "US", "industry": "Semiconductors", "market_cap_usd": 180_000_000_000},
    {"symbol": "INTC", "name": "Intel", "market": "US", "industry": "Semiconductors", "market_cap_usd": 130_000_000_000},
    {"symbol": "SMCI", "name": "Super Micro Computer", "market": "US", "industry": "Technology", "market_cap_usd": 60_000_000_000},
    {"symbol": "PLTR", "name": "Palantir", "market": "US", "industry": "Technology", "market_cap_usd": 250_000_000_000},
    {"symbol": "ASML", "name": "ASML", "market": "US", "industry": "Semiconductors", "market_cap_usd": 390_000_000_000},
    {"symbol": "AMAT", "name": "Applied Materials", "market": "US", "industry": "Semiconductors", "market_cap_usd": 190_000_000_000},
    {"symbol": "LRCX", "name": "Lam Research", "market": "US", "industry": "Semiconductors", "market_cap_usd": 140_000_000_000},
    {"symbol": "TSM", "name": "TSMC ADR", "market": "US", "industry": "Semiconductors", "market_cap_usd": 950_000_000_000},
    {"symbol": "DELL", "name": "Dell Technologies", "market": "US", "industry": "Technology", "market_cap_usd": 90_000_000_000},
    {"symbol": "2330.TW", "name": "TSMC", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 950_000_000_000},
    {"symbol": "2317.TW", "name": "Hon Hai", "market": "TW", "industry": "Technology", "market_cap_usd": 95_000_000_000},
    {"symbol": "2454.TW", "name": "MediaTek", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 75_000_000_000},
    {"symbol": "2308.TW", "name": "Delta Electronics", "market": "TW", "industry": "Technology", "market_cap_usd": 45_000_000_000},
    {"symbol": "2382.TW", "name": "Quanta Computer", "market": "TW", "industry": "Technology", "market_cap_usd": 35_000_000_000},
    {"symbol": "2412.TW", "name": "Chunghwa Telecom", "market": "TW", "industry": "Communication", "market_cap_usd": 30_000_000_000},
    {"symbol": "2881.TW", "name": "Fubon Financial", "market": "TW", "industry": "Financials", "market_cap_usd": 30_000_000_000},
    {"symbol": "2882.TW", "name": "Cathay Financial", "market": "TW", "industry": "Financials", "market_cap_usd": 28_000_000_000},
    {"symbol": "2303.TW", "name": "UMC", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 18_000_000_000},
    {"symbol": "0050.TW", "name": "Yuanta Taiwan 50 ETF", "market": "TW", "industry": "ETF", "market_cap_usd": 16_000_000_000},
    {"symbol": "006208.TW", "name": "Fubon Taiwan 50 ETF", "market": "TW", "industry": "ETF", "market_cap_usd": 9_000_000_000},
    {"symbol": "3711.TW", "name": "ASE Technology", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 22_000_000_000},
    {"symbol": "2603.TW", "name": "Evergreen Marine", "market": "TW", "industry": "Shipping", "market_cap_usd": 15_000_000_000},
    {"symbol": "2609.TW", "name": "Yang Ming Marine", "market": "TW", "industry": "Shipping", "market_cap_usd": 8_000_000_000},
    {"symbol": "2615.TW", "name": "Wan Hai Lines", "market": "TW", "industry": "Shipping", "market_cap_usd": 7_000_000_000},
    {"symbol": "6669.TW", "name": "Wiwynn", "market": "TW", "industry": "Technology", "market_cap_usd": 14_000_000_000},
    {"symbol": "3231.TW", "name": "Wistron", "market": "TW", "industry": "Technology", "market_cap_usd": 11_000_000_000},
    {"symbol": "2356.TW", "name": "Inventec", "market": "TW", "industry": "Technology", "market_cap_usd": 6_500_000_000},
]

SETUP_SIGNALS = [
    {"key": "all", "label": "全部型態", "description": "不限制型態。"},
    {"key": "golden_cross_continuation", "label": "黃金交叉後延續", "description": "MA20 上穿 MA60 後，價格仍站上均線並延續上行。"},
    {"key": "golden_cross_watch", "label": "黃金交叉觀察", "description": "剛出現黃金交叉，但動能或量能還不夠乾淨。"},
    {"key": "death_cross_continuation", "label": "死亡交叉後延續", "description": "MA20 下穿 MA60 後，價格壓在均線下並延續走弱。"},
    {"key": "death_cross_watch", "label": "死亡交叉觀察", "description": "剛出現死亡交叉，需要警戒但尚未完全確認。"},
    {"key": "bullish_continuation", "label": "多頭排列延續", "description": "價格、MA5、MA20、MA60 由上而下排列。"},
    {"key": "bearish_continuation", "label": "空頭排列延續", "description": "價格、MA5、MA20、MA60 由下而上排列。"},
    {"key": "volume_breakout", "label": "帶量突破", "description": "價格站上短均且成交量明顯放大。"},
    {"key": "pullback_hold", "label": "回測均線守住", "description": "回測 MA20 附近後仍收在支撐上。"},
    {"key": "oversold_rebound", "label": "超跌反彈", "description": "RSI 偏低後價格嘗試站回短均。"},
    {"key": "overheat_risk", "label": "過熱風險", "description": "RSI 過熱或短線漲幅過大。"},
    {"key": "mixed", "label": "混合/盤整", "description": "均線糾結或訊號互相抵銷。"},
]


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("openkiri.html", {"request": request})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "OpenKiri"}


@app.get("/api/screener/options")
def screener_options() -> dict[str, Any]:
    return {
        "markets": sorted({item["market"] for item in STOCK_UNIVERSE}),
        "industries": sorted({item["industry"] for item in STOCK_UNIVERSE}),
        "setup_signals": SETUP_SIGNALS,
    }


@app.get("/api/analyze")
def analyze(
    symbol: str = Query("AAPL", min_length=1, max_length=24),
    period: str = Query("6mo", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    interval: str = Query("1d", pattern="^(1d|1wk)$"),
) -> dict[str, Any]:
    resolved = normalize_symbol(symbol)
    rows = fetch_chart(resolved, period, interval)
    if len(rows) < 30:
        raise HTTPException(status_code=404, detail=f"Not enough market data for {resolved}.")
    item = find_universe_item(resolved) or {"symbol": resolved, "name": resolved, "market": market_of(resolved), "industry": "Custom"}
    return build_analysis(item, rows)


@app.get("/api/screener")
def screener(
    markets: str = Query("US,TW", max_length=32),
    industries: str = Query("", max_length=240),
    setup: str = Query("all", max_length=64),
    min_price: float | None = Query(None, ge=0),
    max_price: float | None = Query(None, ge=0),
    sort_by: str = Query("signal", pattern="^(signal|risk|change|market_cap|pe|price|volume)$"),
    limit: int = Query(30, ge=1, le=80),
) -> dict[str, Any]:
    market_set = {x.strip().upper() for x in markets.split(",") if x.strip()}
    industry_set = {x.strip() for x in industries.split(",") if x.strip()}
    selected_setup = setup if setup in {x["key"] for x in SETUP_SIGNALS} else "all"

    candidates = [
        item for item in STOCK_UNIVERSE
        if item["market"] in market_set and (not industry_set or item["industry"] in industry_set)
    ][:80]
    rows = []
    for item in candidates:
        try:
            analysis = build_analysis(item, fetch_chart(item["symbol"], "6mo", "1d"), slim=True)
        except Exception:
            continue
        price = analysis["latest"]["close"]
        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
            continue
        if selected_setup != "all" and analysis["setup_signal"]["key"] != selected_setup:
            continue
        rows.append(analysis)

    def sort_key(row: dict[str, Any]) -> float:
        val = row.get("valuation", {})
        latest = row.get("latest", {})
        if sort_by == "risk":
            return -float(row["risk"]["score"])
        if sort_by == "change":
            return float(row["change"]["pct"])
        if sort_by == "market_cap":
            return float(val.get("market_cap") or row.get("market_cap_usd") or 0)
        if sort_by == "pe":
            pe = val.get("trailing_pe") or val.get("forward_pe")
            return -float(pe or 9_999)
        if sort_by == "price":
            return float(latest.get("close") or 0)
        if sort_by == "volume":
            return float(latest.get("volume_ratio") or 0)
        return float(row["setup_signal"].get("score") or 0)

    rows.sort(key=sort_key, reverse=True)
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "setup": selected_setup,
        "sort_by": sort_by,
        "count": len(rows[:limit]),
        "rows": rows[:limit],
        "note": "Market cap and P/E are refreshed from Yahoo when available, with static universe values as fallback.",
    }


@app.get("/api/recommendations")
def recommendations(markets: str = "US,TW", limit: int = 10) -> dict[str, Any]:
    return screener(markets=markets, setup="all", sort_by="signal", limit=limit)


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if s.isdigit() and len(s) == 4:
        return f"{s}.TW"
    return s


def market_of(symbol: str) -> str:
    return "TW" if symbol.endswith((".TW", ".TWO")) else "US"


def find_universe_item(symbol: str) -> dict[str, Any] | None:
    return next((item for item in STOCK_UNIVERSE if item["symbol"] == symbol), None)


def fetch_chart(symbol: str, period: str, interval: str) -> list[dict[str, Any]]:
    key = (symbol, period, interval)
    cached = CHART_CACHE.get(key)
    if cached and time.time() - cached[0] < 300:
        return cached[1]

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    response = requests.get(url, params={"range": period, "interval": interval, "includePrePost": "false"}, headers=HEADERS, timeout=12)
    response.raise_for_status()
    result = response.json().get("chart", {}).get("result", [])
    if not result:
        raise HTTPException(status_code=404, detail=f"No chart data for {symbol}.")

    data = result[0]
    timestamps = data.get("timestamp") or []
    quote = (data.get("indicators", {}).get("quote") or [{}])[0]
    rows: list[dict[str, Any]] = []
    for i, ts in enumerate(timestamps):
        close = pick_number(quote.get("close"), i)
        high = pick_number(quote.get("high"), i)
        low = pick_number(quote.get("low"), i)
        open_price = pick_number(quote.get("open"), i)
        if close is None or high is None or low is None or open_price is None:
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d"),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": int(pick_number(quote.get("volume"), i) or 0),
            }
        )
    rows = add_indicators(rows)
    CHART_CACHE[key] = (time.time(), rows)
    return rows


def pick_number(values: list[Any] | None, index: int) -> float | None:
    if not values or index >= len(values):
        return None
    value = values[index]
    if value is None:
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def add_indicators(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = [row["close"] for row in rows]
    prev_ema12 = prev_ema26 = prev_signal = None
    gains: list[float] = []
    losses: list[float] = []
    trs: list[float] = []
    for i, row in enumerate(rows):
        close = row["close"]
        prev_close = rows[i - 1]["close"] if i else close
        change = close - prev_close
        gains.append(max(0, change))
        losses.append(max(0, -change))
        tr = max(row["high"] - row["low"], abs(row["high"] - prev_close), abs(row["low"] - prev_close))
        trs.append(tr)
        row["ma5"] = avg(closes[max(0, i - 4): i + 1])
        row["ma20"] = avg(closes[max(0, i - 19): i + 1])
        row["ma60"] = avg(closes[max(0, i - 59): i + 1])
        row["rsi14"] = rsi(gains[-14:], losses[-14:])
        row["atr14"] = avg(trs[-14:])
        row["ret5"] = pct(closes, i, 5)
        row["ret20"] = pct(closes, i, 20)
        row["volume_avg20"] = avg([x["volume"] for x in rows[max(0, i - 19): i + 1]])
        row["volume_ratio"] = row["volume"] / row["volume_avg20"] if row["volume_avg20"] else 0
        prev_ema12 = ema(close, prev_ema12, 12)
        prev_ema26 = ema(close, prev_ema26, 26)
        macd = prev_ema12 - prev_ema26
        prev_signal = ema(macd, prev_signal, 9)
        row["macd"] = macd
        row["macd_signal"] = prev_signal
    return rows


def build_analysis(item: dict[str, Any], rows: list[dict[str, Any]], slim: bool = False) -> dict[str, Any]:
    latest = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else latest
    valuation = stock_valuation(item)
    risk = build_risk(rows, latest, valuation)
    setup = build_setup_signal(rows, latest, risk)
    score = recommendation_score(latest, risk, setup, valuation)
    response = {
        "ok": True,
        "symbol": item["symbol"],
        "name": item.get("name", item["symbol"]),
        "market": item.get("market", market_of(item["symbol"])),
        "industry": item.get("industry", "Custom"),
        "market_cap_usd": item.get("market_cap_usd"),
        "latest": {
            "date": latest["date"],
            "close": number(latest["close"]),
            "volume": int(latest.get("volume") or 0),
            "volume_ratio": number(latest.get("volume_ratio")),
            "rsi14": number(latest.get("rsi14")),
            "ma20": number(latest.get("ma20")),
            "ma60": number(latest.get("ma60")),
        },
        "change": {
            "amount": number(latest["close"] - prev["close"]),
            "pct": number(((latest["close"] / prev["close"]) - 1) * 100 if prev["close"] else 0),
            "ret20": number((latest.get("ret20") or 0) * 100),
        },
        "risk": risk,
        "setup_signal": setup,
        "valuation": valuation,
        "score": score,
    }
    if not slim:
        response["chart"] = [
            {
                "date": row["date"],
                "open": number(row["open"]),
                "high": number(row["high"]),
                "low": number(row["low"]),
                "close": number(row["close"]),
                "volume": int(row.get("volume") or 0),
                "ma20": number(row.get("ma20")),
                "ma60": number(row.get("ma60")),
            }
            for row in rows[-140:]
        ]
        response["chart_full_length"] = len(rows)
    return response


def build_risk(rows: list[dict[str, Any]], latest: dict[str, Any], valuation: dict[str, Any]) -> dict[str, Any]:
    score = 45
    reasons: list[str] = []
    close = latest["close"]
    if close > latest["ma20"] > latest["ma60"]:
        score -= 12
        reasons.append("價格站上 MA20/MA60")
    elif close < latest["ma20"] < latest["ma60"]:
        score += 16
        reasons.append("價格跌破 MA20/MA60")
    if latest["rsi14"] >= 72:
        score += 13
        reasons.append("RSI 過熱")
    elif latest["rsi14"] <= 32:
        score += 8
        reasons.append("RSI 偏弱或超跌")
    if latest["macd"] > latest["macd_signal"]:
        score -= 5
        reasons.append("MACD 偏多")
    else:
        score += 5
        reasons.append("MACD 偏弱")
    if latest["volume_ratio"] >= 1.8:
        score += 5
        reasons.append("成交量異常放大")
    pe = valuation.get("trailing_pe") or valuation.get("forward_pe")
    if pe and pe >= 70:
        score += 8
        reasons.append("P/E 偏高")
    elif pe and pe <= 25:
        score -= 4
        reasons.append("P/E 未明顯過熱")
    score = int(max(0, min(100, score)))
    trend = "bullish" if close > latest["ma20"] and latest["macd"] > latest["macd_signal"] else "bearish" if close < latest["ma20"] else "sideways"
    level = "low" if score < 40 else "medium" if score < 70 else "high"
    return {"score": score, "level": level, "trend": trend, "reasons": reasons[:5]}


def build_setup_signal(rows: list[dict[str, Any]], latest: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    crosses = find_crosses(rows)
    recent_cross = crosses[-1] if crosses else None
    bars_since = len(rows) - 1 - recent_cross["index"] if recent_cross else None
    close, ma20, ma60 = latest["close"], latest["ma20"], latest["ma60"]
    ma5 = latest["ma5"]
    slope20 = slope_pct([row["ma20"] for row in rows[-12:]], close)
    slope60 = slope_pct([row["ma60"] for row in rows[-24:]], close)
    bullish_stack = close > ma5 > ma20 > ma60
    bearish_stack = close < ma5 < ma20 < ma60
    volume_breakout = close > ma20 and latest["volume_ratio"] >= 1.65 and latest["ret5"] > 0.025
    pullback_hold = abs(close / ma20 - 1) <= 0.035 and close >= ma20 and slope20 >= -0.02
    oversold_rebound = latest["rsi14"] <= 36 and close >= ma5
    overheat = latest["rsi14"] >= 74 or latest["ret20"] >= 0.16

    key = "mixed"
    tone = "neutral"
    score = 45
    reason = "均線與動能互相抵銷，暫時歸類為盤整。"
    if recent_cross and bars_since is not None and bars_since <= 35 and recent_cross["type"] == "golden_cross":
        if close >= ma20 and slope20 > 0 and risk["trend"] != "bearish":
            key, tone, score, reason = "golden_cross_continuation", "bullish", 82, "黃金交叉後仍站上 MA20，短期均線斜率向上。"
        else:
            key, tone, score, reason = "golden_cross_watch", "watch", 62, "黃金交叉剛出現，但價格或斜率仍需要確認。"
    elif recent_cross and bars_since is not None and bars_since <= 35 and recent_cross["type"] == "death_cross":
        if close <= ma20 and slope20 < 0 and risk["trend"] != "bullish":
            key, tone, score, reason = "death_cross_continuation", "bearish", 82, "死亡交叉後價格仍壓在 MA20 下，弱勢延續機率提高。"
        else:
            key, tone, score, reason = "death_cross_watch", "watch", 64, "死亡交叉剛出現，但反彈或量能讓訊號尚未完全確認。"
    elif bullish_stack and slope20 > 0 and slope60 >= -0.01:
        key, tone, score, reason = "bullish_continuation", "bullish", 78, "多頭排列且 MA20 斜率向上。"
    elif bearish_stack and slope20 < 0 and slope60 <= 0.02:
        key, tone, score, reason = "bearish_continuation", "bearish", 78, "空頭排列且 MA20 斜率向下。"
    elif volume_breakout:
        key, tone, score, reason = "volume_breakout", "bullish", 74, "價格站上 MA20 且成交量明顯放大。"
    elif pullback_hold:
        key, tone, score, reason = "pullback_hold", "bullish", 68, "回測 MA20 附近仍守住，適合列入觀察。"
    elif oversold_rebound:
        key, tone, score, reason = "oversold_rebound", "watch", 61, "RSI 偏低後嘗試站回短均，屬反彈觀察。"
    elif overheat:
        key, tone, score, reason = "overheat_risk", "bearish", 66, "短線過熱，追價風險提高。"

    return {
        "key": key,
        "label": next((x["label"] for x in SETUP_SIGNALS if x["key"] == key), "混合/盤整"),
        "tone": tone,
        "score": score,
        "reason": reason,
        "latest_cross": recent_cross,
        "bars_since_cross": bars_since,
    }


def find_crosses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    crosses = []
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if prev["ma20"] <= prev["ma60"] and cur["ma20"] > cur["ma60"]:
            crosses.append({"index": i, "date": cur["date"], "type": "golden_cross"})
        elif prev["ma20"] >= prev["ma60"] and cur["ma20"] < cur["ma60"]:
            crosses.append({"index": i, "date": cur["date"], "type": "death_cross"})
    return crosses[-6:]


def stock_valuation(item: dict[str, Any]) -> dict[str, Any]:
    symbol = item["symbol"]
    cached = VALUATION_CACHE.get(symbol)
    if cached and time.time() - cached[0] < 1800:
        return cached[1]

    payload = {
        "currency": "USD",
        "market_cap": item.get("market_cap_usd"),
        "trailing_pe": None,
        "forward_pe": None,
        "source": "static-universe",
        "fallback": True,
    }
    try:
        series = fetch_fundamental_series(symbol)
        ts_cap = latest_series_value(series, "quarterlyMarketCap") or latest_series_value(series, "trailingMarketCap")
        ts_pe = latest_series_value(series, "trailingPeRatio")
        if ts_cap or ts_pe:
            static_cap = item.get("market_cap_usd")
            cap_value = ts_cap
            cap_fallback = False
            if cap_value and static_cap and (cap_value > static_cap * 4 or cap_value < static_cap / 4):
                cap_value = static_cap
                cap_fallback = True
            payload.update(
                {
                    "currency": "TWD" if item.get("market") == "TW" else "USD",
                    "market_cap": optional_number(cap_value or payload.get("market_cap"), 0),
                    "trailing_pe": optional_number(ts_pe),
                    "source": "yahoo-fundamentals-timeseries",
                    "fallback": cap_fallback,
                }
            )
    except Exception:
        pass

    try:
        url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
        modules = "price,summaryDetail,defaultKeyStatistics"
        response = requests.get(url, params={"modules": modules}, headers=HEADERS, timeout=10)
        response.raise_for_status()
        result = response.json().get("quoteSummary", {}).get("result", [])
        data = result[0] if result else {}
        price = data.get("price", {})
        detail = data.get("summaryDetail", {})
        stats = data.get("defaultKeyStatistics", {})
        yahoo_cap = raw(price.get("marketCap")) or raw(detail.get("marketCap"))
        trailing_pe = raw(detail.get("trailingPE")) or raw(stats.get("trailingPE"))
        if yahoo_cap:
            payload["market_cap"] = optional_number(yahoo_cap, 0)
            payload["currency"] = price.get("currency") or payload.get("currency") or "USD"
            payload["fallback"] = False
        if trailing_pe:
            payload["trailing_pe"] = optional_number(trailing_pe)
            payload["fallback"] = False
        payload["forward_pe"] = optional_number(raw(stats.get("forwardPE"))) or payload.get("forward_pe")
        if not payload.get("fallback"):
            payload["source"] = "yahoo-quoteSummary" if yahoo_cap or trailing_pe else payload["source"]
    except Exception:
        pass
    VALUATION_CACHE[symbol] = (time.time(), payload)
    return payload


def fetch_fundamental_series(symbol: str) -> list[dict[str, Any]]:
    now = int(time.time())
    start = now - 370 * 24 * 60 * 60
    modules = "trailingPeRatio,trailingMarketCap,quarterlyMarketCap"
    url = f"https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
    response = requests.get(
        url,
        params={"symbol": symbol, "type": modules, "period1": start, "period2": now},
        headers=HEADERS,
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("timeseries", {}).get("result") or []


def latest_series_value(series: list[dict[str, Any]], key: str) -> float | None:
    for block in series:
        values = block.get(key)
        if not values:
            continue
        for item in reversed(values):
            value = raw(item.get("reportedValue"))
            if value is not None:
                return optional_number(value, 6)
    return None


def recommendation_score(latest: dict[str, Any], risk: dict[str, Any], setup: dict[str, Any], valuation: dict[str, Any]) -> int:
    pe = valuation.get("trailing_pe") or valuation.get("forward_pe")
    pe_bonus = 6 if pe and pe <= 25 else -8 if pe and pe >= 70 else 0
    value = setup["score"] * 0.48 + (100 - risk["score"]) * 0.32 + min(18, latest["volume_ratio"] * 6) + pe_bonus
    return int(max(0, min(100, round(value))))


def raw(value: Any) -> Any:
    if isinstance(value, dict):
        return value.get("raw")
    return value


def avg(values: list[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def pct(values: list[float], i: int, periods: int) -> float:
    if i < periods or values[i - periods] == 0:
        return 0.0
    return values[i] / values[i - periods] - 1


def rsi(gains: list[float], losses: list[float]) -> float:
    ag = avg(gains)
    al = avg(losses)
    if al == 0:
        return 100.0 if ag else 50.0
    rs = ag / al
    return 100 - (100 / (1 + rs))


def ema(value: float, previous: float | None, span: int) -> float:
    if previous is None:
        return value
    k = 2 / (span + 1)
    return value * k + previous * (1 - k)


def slope_pct(values: list[float], close: float) -> float:
    if len(values) < 2 or not close:
        return 0
    return (values[-1] - values[0]) / close * 100


def number(value: Any, digits: int = 2) -> float:
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return 0
        return round(value, digits)
    except Exception:
        return 0


def optional_number(value: Any, digits: int = 2) -> float | None:
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, digits)
    except Exception:
        return None
