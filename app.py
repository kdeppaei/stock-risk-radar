from __future__ import annotations

import json
import math
import re
import secrets
import sqlite3
import time
import html as html_lib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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

app = FastAPI(title="Stock Risk Radar", version="4.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
MOVERS_CACHE: dict[tuple[str, int, str], tuple[float, dict[str, Any]]] = {}
EVENT_ALERTS_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
SERENITY_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
SERENITY_RECENT_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}

BOT_DEFS: list[dict[str, Any]] = [
    {"id": "steady_turtle", "name": "Steady Turtle", "style": "conservative", "risk": 26, "idea": "Trend + low drawdown filter. Good for users who hate big swings."},
    {"id": "value_guard", "name": "Value Guard", "style": "conservative", "risk": 32, "idea": "Quality, market-cap and controlled risk. Slower, less flashy."},
    {"id": "balanced_compass", "name": "Balanced Compass", "style": "balanced", "risk": 48, "idea": "Blends trend, quality and volume confirmation."},
    {"id": "rocket_breakout", "name": "Rocket Breakout", "style": "aggressive", "risk": 76, "idea": "Momentum and breakout hunter. Higher upside, higher whipsaw risk."},
    {"id": "dip_reversal", "name": "Dip Reversal", "style": "balanced", "risk": 58, "idea": "Looks for pullbacks that still hold key moving averages."},
    {"id": "chip_hunter", "name": "Chip Hunter", "style": "aggressive", "risk": 82, "idea": "Semiconductor and AI supply-chain specialist; very correlated in chip cycles."},
]

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

STOCK_UNIVERSE = [
    {"symbol": "NVDA", "name": "NVIDIA", "market": "US", "industry": "Semiconductors", "market_cap_usd": 3800000000000},
    {"symbol": "AAPL", "name": "Apple", "market": "US", "industry": "Technology", "market_cap_usd": 3200000000000},
    {"symbol": "MSFT", "name": "Microsoft", "market": "US", "industry": "Technology", "market_cap_usd": 3100000000000},
    {"symbol": "GOOGL", "name": "Alphabet", "market": "US", "industry": "Communication", "market_cap_usd": 2300000000000},
    {"symbol": "AMZN", "name": "Amazon", "market": "US", "industry": "Consumer", "market_cap_usd": 2200000000000},
    {"symbol": "META", "name": "Meta Platforms", "market": "US", "industry": "Communication", "market_cap_usd": 1700000000000},
    {"symbol": "AVGO", "name": "Broadcom", "market": "US", "industry": "Semiconductors", "market_cap_usd": 1500000000000},
    {"symbol": "TSLA", "name": "Tesla", "market": "US", "industry": "Automotive", "market_cap_usd": 1100000000000},
    {"symbol": "BRK-B", "name": "Berkshire Hathaway", "market": "US", "industry": "Financials", "market_cap_usd": 1000000000000},
    {"symbol": "JPM", "name": "JPMorgan Chase", "market": "US", "industry": "Financials", "market_cap_usd": 750000000000},
    {"symbol": "LLY", "name": "Eli Lilly", "market": "US", "industry": "Healthcare", "market_cap_usd": 720000000000},
    {"symbol": "V", "name": "Visa", "market": "US", "industry": "Financials", "market_cap_usd": 650000000000},
    {"symbol": "MA", "name": "Mastercard", "market": "US", "industry": "Financials", "market_cap_usd": 510000000000},
    {"symbol": "WMT", "name": "Walmart", "market": "US", "industry": "Retail", "market_cap_usd": 800000000000},
    {"symbol": "ORCL", "name": "Oracle", "market": "US", "industry": "Technology", "market_cap_usd": 500000000000},
    {"symbol": "NFLX", "name": "Netflix", "market": "US", "industry": "Communication", "market_cap_usd": 450000000000},
    {"symbol": "AMD", "name": "Advanced Micro Devices", "market": "US", "industry": "Semiconductors", "market_cap_usd": 330000000000},
    {"symbol": "MU", "name": "Micron Technology", "market": "US", "industry": "Semiconductors", "market_cap_usd": 170000000000},
    {"symbol": "QCOM", "name": "Qualcomm", "market": "US", "industry": "Semiconductors", "market_cap_usd": 180000000000},
    {"symbol": "INTC", "name": "Intel", "market": "US", "industry": "Semiconductors", "market_cap_usd": 130000000000},
    {"symbol": "SMCI", "name": "Super Micro Computer", "market": "US", "industry": "Technology", "market_cap_usd": 60000000000},
    {"symbol": "PLTR", "name": "Palantir", "market": "US", "industry": "Technology", "market_cap_usd": 250000000000},
    {"symbol": "COIN", "name": "Coinbase", "market": "US", "industry": "Financials", "market_cap_usd": 80000000000},
    {"symbol": "MSTR", "name": "MicroStrategy", "market": "US", "industry": "Technology", "market_cap_usd": 90000000000},
    {"symbol": "UNH", "name": "UnitedHealth", "market": "US", "industry": "Healthcare", "market_cap_usd": 450000000000},
    {"symbol": "JNJ", "name": "Johnson & Johnson", "market": "US", "industry": "Healthcare", "market_cap_usd": 390000000000},
    {"symbol": "XOM", "name": "Exxon Mobil", "market": "US", "industry": "Energy", "market_cap_usd": 500000000000},
    {"symbol": "CVX", "name": "Chevron", "market": "US", "industry": "Energy", "market_cap_usd": 280000000000},
    {"symbol": "CAT", "name": "Caterpillar", "market": "US", "industry": "Industrials", "market_cap_usd": 180000000000},
    {"symbol": "GE", "name": "GE Aerospace", "market": "US", "industry": "Industrials", "market_cap_usd": 270000000000},
    {"symbol": "2330.TW", "name": "TSMC", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 950000000000},
    {"symbol": "2317.TW", "name": "Hon Hai", "market": "TW", "industry": "Technology", "market_cap_usd": 95000000000},
    {"symbol": "2454.TW", "name": "MediaTek", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 75000000000},
    {"symbol": "2308.TW", "name": "Delta Electronics", "market": "TW", "industry": "Technology", "market_cap_usd": 45000000000},
    {"symbol": "2382.TW", "name": "Quanta Computer", "market": "TW", "industry": "Technology", "market_cap_usd": 35000000000},
    {"symbol": "2412.TW", "name": "Chunghwa Telecom", "market": "TW", "industry": "Communication", "market_cap_usd": 30000000000},
    {"symbol": "2881.TW", "name": "Fubon Financial", "market": "TW", "industry": "Financials", "market_cap_usd": 30000000000},
    {"symbol": "2882.TW", "name": "Cathay Financial", "market": "TW", "industry": "Financials", "market_cap_usd": 28000000000},
    {"symbol": "2303.TW", "name": "UMC", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 18000000000},
    {"symbol": "3711.TW", "name": "ASE Technology", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 22000000000},
    {"symbol": "2886.TW", "name": "Mega Financial", "market": "TW", "industry": "Financials", "market_cap_usd": 19000000000},
    {"symbol": "2891.TW", "name": "CTBC Financial", "market": "TW", "industry": "Financials", "market_cap_usd": 22000000000},
    {"symbol": "1303.TW", "name": "Nan Ya Plastics", "market": "TW", "industry": "Materials", "market_cap_usd": 12000000000},
    {"symbol": "1301.TW", "name": "Formosa Plastics", "market": "TW", "industry": "Materials", "market_cap_usd": 13000000000},
    {"symbol": "2002.TW", "name": "China Steel", "market": "TW", "industry": "Materials", "market_cap_usd": 11000000000},
    {"symbol": "1216.TW", "name": "Uni-President", "market": "TW", "industry": "Consumer", "market_cap_usd": 14000000000},
    {"symbol": "2207.TW", "name": "Hotai Motor", "market": "TW", "industry": "Automotive", "market_cap_usd": 11000000000},
    {"symbol": "2603.TW", "name": "Evergreen Marine", "market": "TW", "industry": "Shipping", "market_cap_usd": 15000000000},
    {"symbol": "2615.TW", "name": "Wan Hai Lines", "market": "TW", "industry": "Shipping", "market_cap_usd": 7000000000},
    {"symbol": "2609.TW", "name": "Yang Ming Marine", "market": "TW", "industry": "Shipping", "market_cap_usd": 8000000000},
    {"symbol": "5871.TW", "name": "Chailease", "market": "TW", "industry": "Financials", "market_cap_usd": 8000000000},
    {"symbol": "6488.TWO", "name": "GlobalWafers", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 7000000000},
    {"symbol": "3034.TW", "name": "Novatek", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 9000000000},
    {"symbol": "2379.TW", "name": "Realtek", "market": "TW", "industry": "Semiconductors", "market_cap_usd": 9000000000},
    {"symbol": "6669.TW", "name": "Wiwynn", "market": "TW", "industry": "Technology", "market_cap_usd": 14000000000},
    {"symbol": "3231.TW", "name": "Wistron", "market": "TW", "industry": "Technology", "market_cap_usd": 11000000000},
    {"symbol": "2356.TW", "name": "Inventec", "market": "TW", "industry": "Technology", "market_cap_usd": 6500000000},
    {"symbol": "2357.TW", "name": "Asustek", "market": "TW", "industry": "Technology", "market_cap_usd": 12000000000},
]

RELATED_ASSETS: dict[str, list[dict[str, Any]]] = {
    "MU": [
        {"symbol": "MUU", "name": "Direxion Daily MU Bull 2X ETF", "type": "leveraged_etf", "relation": "2x long ETF", "structure": "derivative", "why": "Tracks Micron with daily 2x long leverage; useful for high-beta directional monitoring."},
        {"symbol": "SOXL", "name": "Direxion Daily Semiconductor Bull 3X", "type": "leveraged_etf", "relation": "semiconductor 3x basket", "structure": "sector_beta", "why": "Leveraged semiconductor basket; often moves with memory and AI chip risk appetite."},
        {"symbol": "SOXS", "name": "Direxion Daily Semiconductor Bear 3X", "type": "inverse_etf", "relation": "semiconductor inverse hedge", "structure": "sector_beta", "why": "Inverse leveraged semiconductor ETF; rising SOXS can imply sector selling pressure."},
        {"symbol": "NVDA", "name": "NVIDIA", "type": "customer_demand", "relation": "AI/server demand driver", "structure": "vertical", "why": "AI server demand can support memory and HBM cycles."},
        {"symbol": "AMD", "name": "AMD", "type": "customer_peer", "relation": "AI/CPU platform demand", "structure": "vertical", "why": "Data-center platform demand affects memory attachment and chip-cycle sentiment."},
        {"symbol": "WDC", "name": "Western Digital", "type": "peer", "relation": "memory/storage peer", "structure": "horizontal", "why": "Storage cycle and NAND pricing can move with memory-sector expectations."},
        {"symbol": "STX", "name": "Seagate", "type": "peer", "relation": "storage peer", "structure": "horizontal", "why": "Storage demand provides a related read-through for enterprise hardware demand."},
        {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "type": "sector_etf", "relation": "semiconductor ETF", "structure": "sector_beta", "why": "Broad semiconductor ETF for sector-level confirmation."},
    ],
    "NVDA": [
        {"symbol": "NVDL", "name": "GraniteShares 2x Long NVDA", "type": "leveraged_etf", "relation": "2x long ETF", "structure": "derivative", "why": "Single-stock leveraged ETF; reflects high-beta NVDA speculation."},
        {"symbol": "NVDS", "name": "AXS 1.25X NVDA Bear", "type": "inverse_etf", "relation": "inverse NVDA product", "structure": "derivative", "why": "Inverse product; strength can hint at short-term hedging or bearish pressure."},
        {"symbol": "TSM", "name": "TSMC ADR", "type": "supplier", "relation": "foundry supplier", "structure": "vertical", "why": "TSMC manufactures advanced chips for leading AI/GPU demand."},
        {"symbol": "2330.TW", "name": "TSMC Taiwan", "type": "supplier", "relation": "foundry supplier", "structure": "vertical", "why": "Taiwan listing of TSMC; important AI supply-chain read-through."},
        {"symbol": "AVGO", "name": "Broadcom", "type": "peer", "relation": "AI/custom silicon peer", "structure": "horizontal", "why": "Competes in AI connectivity/custom silicon sentiment."},
        {"symbol": "AMD", "name": "AMD", "type": "peer", "relation": "AI accelerator peer", "structure": "horizontal", "why": "GPU/AI accelerator peer for horizontal comparison."},
        {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "type": "sector_etf", "relation": "semiconductor ETF", "structure": "sector_beta", "why": "Sector ETF for confirming whether move is stock-specific or sector-wide."},
    ],
    "TSLA": [
        {"symbol": "TSLL", "name": "Direxion Daily TSLA Bull 2X", "type": "leveraged_etf", "relation": "2x long ETF", "structure": "derivative", "why": "Single-stock leveraged ETF; reflects high-beta Tesla speculation."},
        {"symbol": "TSLQ", "name": "AXS TSLA Bear Daily ETF", "type": "inverse_etf", "relation": "inverse TSLA product", "structure": "derivative", "why": "Inverse product; strength can imply hedging or bearish positioning."},
        {"symbol": "GM", "name": "General Motors", "type": "peer", "relation": "auto peer", "structure": "horizontal", "why": "Traditional auto peer for EV/auto demand comparison."},
        {"symbol": "F", "name": "Ford", "type": "peer", "relation": "auto peer", "structure": "horizontal", "why": "Auto-cycle and EV pricing pressure comparison."},
        {"symbol": "LI", "name": "Li Auto", "type": "peer", "relation": "China EV peer", "structure": "horizontal", "why": "China EV demand and pricing read-through."},
        {"symbol": "ALB", "name": "Albemarle", "type": "supplier", "relation": "battery material supplier", "structure": "vertical", "why": "Lithium supply chain can influence EV margin expectations."},
    ],
    "2330.TW": [
        {"symbol": "TSM", "name": "TSMC ADR", "type": "adr", "relation": "US ADR", "structure": "same_company", "why": "US-listed ADR for the same company; useful during US market hours."},
        {"symbol": "NVDA", "name": "NVIDIA", "type": "customer", "relation": "AI chip customer", "structure": "vertical", "why": "Advanced AI chip demand is a key foundry demand driver."},
        {"symbol": "AAPL", "name": "Apple", "type": "customer", "relation": "consumer chip customer", "structure": "vertical", "why": "Smartphone/device cycle can affect advanced-node utilization."},
        {"symbol": "ASML", "name": "ASML", "type": "supplier", "relation": "lithography supplier", "structure": "vertical", "why": "EUV equipment supplier; capex and leading-node supply chain read-through."},
        {"symbol": "2454.TW", "name": "MediaTek", "type": "customer_peer", "relation": "IC design customer", "structure": "vertical", "why": "Fabless IC demand maps into foundry orders."},
        {"symbol": "2303.TW", "name": "UMC", "type": "peer", "relation": "foundry peer", "structure": "horizontal", "why": "Mature-node foundry peer for cycle comparison."},
        {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "type": "sector_etf", "relation": "semiconductor ETF", "structure": "sector_beta", "why": "US semiconductor ETF for sector confirmation."},
    ],
    "2454.TW": [
        {"symbol": "2330.TW", "name": "TSMC", "type": "supplier", "relation": "foundry supplier", "structure": "vertical", "why": "MediaTek relies on foundry capacity and advanced process availability."},
        {"symbol": "QCOM", "name": "Qualcomm", "type": "peer", "relation": "mobile chipset peer", "structure": "horizontal", "why": "Smartphone chipset competitor and demand benchmark."},
        {"symbol": "2379.TW", "name": "Realtek", "type": "peer", "relation": "IC design peer", "structure": "horizontal", "why": "Taiwan IC design peer for local sector sentiment."},
        {"symbol": "3034.TW", "name": "Novatek", "type": "peer", "relation": "IC design peer", "structure": "horizontal", "why": "IC design peer for demand-cycle comparison."},
    ],
}

SERENITY_LINKS = {
    "x_profile": "https://x.com/aleabitoreddit",
    "github": "https://github.com/haskaomni/serenity",
    "capafy": "https://capafy.ai/conversations?id=preview-2521387714",
}

SERENITY_TOPICS: dict[str, dict[str, Any]] = {
    "ai_infra_neocloud": {
        "label": "AI infrastructure / neocloud",
        "keywords": ["ai", "gpu", "accelerator", "datacenter", "data center", "server", "compute", "inference", "training", "hyperscaler", "cloud"],
    },
    "memory_storage": {
        "label": "Memory / storage cycle",
        "keywords": ["memory", "dram", "hbm", "nand", "ssd", "storage", "micron", "hynix", "samsung"],
    },
    "optical_networking": {
        "label": "Optical / networking bottleneck",
        "keywords": ["optical", "photonics", "transceiver", "800g", "1.6t", "ethernet", "infiniband", "networking", "switch"],
    },
    "semi_supply_chain": {
        "label": "Semiconductor supply chain",
        "keywords": ["semiconductor", "foundry", "wafer", "packaging", "substrate", "euv", "lithography", "asic", "fabless", "chip"],
    },
    "power_grid_energy": {
        "label": "Power grid / energy constraint",
        "keywords": ["power", "grid", "electricity", "energy", "nuclear", "gas", "transformer", "utility"],
    },
    "robotics_space_industrial": {
        "label": "Robotics / space / industrial",
        "keywords": ["robot", "robotics", "space", "rocket", "defense", "aerospace", "industrial", "automation"],
    },
    "platform_consumer_fintech": {
        "label": "Platform / consumer / fintech",
        "keywords": ["ads", "advertising", "marketplace", "consumer", "fintech", "payment", "brokerage", "stablecoin", "social"],
    },
}

SERENITY_MARKERS: dict[str, list[str]] = {
    "conviction": ["upgrade", "outperform", "buy rating", "long", "position", "strong demand", "record", "beat"],
    "asymmetry": ["mispriced", "undervalued", "cheap", "rerate", "underappreciated", "overlooked", "hidden"],
    "supply_chain": ["supply chain", "bottleneck", "shortage", "capacity", "lead time", "constraint", "duopoly", "monopoly"],
    "catalyst": ["earnings", "guidance", "order", "contract", "launch", "ramp", "mass production", "approval"],
    "risk": ["risk", "dilution", "debt", "lawsuit", "probe", "tariff", "restriction", "competition", "weak demand"],
    "caution": ["downgrade", "underperform", "sell rating", "trim", "too hot", "overvalued", "bubble", "crowded"],
}

SERENITY_SYMBOL_THEMES: dict[str, list[str]] = {
    "NVDA": ["ai_infra_neocloud", "semi_supply_chain"],
    "AMD": ["ai_infra_neocloud", "semi_supply_chain"],
    "AVGO": ["ai_infra_neocloud", "optical_networking", "semi_supply_chain"],
    "MU": ["memory_storage", "ai_infra_neocloud"],
    "MUU": ["memory_storage", "ai_infra_neocloud"],
    "SMCI": ["ai_infra_neocloud"],
    "TSM": ["semi_supply_chain", "ai_infra_neocloud"],
    "2330.TW": ["semi_supply_chain", "ai_infra_neocloud"],
    "2454.TW": ["semi_supply_chain"],
    "2303.TW": ["semi_supply_chain"],
    "6488.TWO": ["semi_supply_chain"],
    "6669.TW": ["ai_infra_neocloud"],
    "2382.TW": ["ai_infra_neocloud"],
    "TSLA": ["robotics_space_industrial", "power_grid_energy"],
    "PLTR": ["ai_infra_neocloud", "platform_consumer_fintech"],
    "COIN": ["platform_consumer_fintech"],
    "MSTR": ["platform_consumer_fintech"],
}


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                code TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                watch_json TEXT NOT NULL DEFAULT '[]',
                bot_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS forum_posts (
                id TEXT PRIMARY KEY,
                room_date TEXT NOT NULL,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
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


@app.get("/api/event-alerts")
def event_alerts(
    symbol: str = Query("", max_length=32),
    days: int = Query(45, ge=1, le=120),
) -> dict[str, Any]:
    raw = symbol.strip().upper()
    normalized = ""
    market = "US"
    if raw:
        normalized, market = normalize_symbol(raw)
    key = (normalized or "MARKET", days)
    now = time.time()
    cached = EVENT_ALERTS_CACHE.get(key)
    if cached and now - cached[0] < 900:
        return cached[1]

    earnings_days = max(days, 90)
    macro_events = fetch_macro_event_alerts(days)
    earnings = fetch_earnings_alerts(normalized, market, earnings_days) if normalized else []
    news_watch = fetch_event_news_watch(normalized, market, days)
    events = sorted(macro_events + earnings + news_watch, key=lambda item: (item.get("date") or "9999-12-31", -int(item.get("score", 0))))
    payload = {
        "ok": True,
        "symbol": normalized,
        "market": market,
        "days": days,
        "earnings_days": earnings_days,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "macro_events": macro_events,
        "earnings": earnings,
        "news_watch": news_watch,
        "events": events[:18],
    }
    EVENT_ALERTS_CACHE[key] = (now, payload)
    return payload


@app.get("/api/serenity")
def serenity_signal(
    symbol: str = Query(..., min_length=1, max_length=32),
    period: str = Query("6mo", pattern="^(3mo|6mo|1y|2y)$"),
) -> dict[str, Any]:
    raw = symbol.strip().upper()
    normalized, market = normalize_symbol(raw)
    cache_key = (normalized, period)
    now = time.time()
    cached = SERENITY_CACHE.get(cache_key)
    if cached and now - cached[0] < 600:
        return cached[1]

    rows: list[dict[str, Any]] = []
    resolved = normalized
    for candidate in candidate_symbols(normalized, raw):
        rows = fetch_price_history(candidate, period, "1d")
        if rows:
            resolved = candidate
            break
    if len(rows) < 40:
        raise HTTPException(status_code=422, detail="Not enough data for Serenity tracker.")

    rows = calculate_indicators(rows)
    latest, previous = rows[-1], rows[-2]
    levels = support_resistance(rows)
    news = fetch_news(resolved, market)
    risk = build_risk(latest, levels, news)
    prediction = build_prediction(rows, latest, risk, news)
    score = build_serenity_signal(resolved, market, rows, latest, previous, risk, prediction, news)
    payload = {
        "ok": True,
        "symbol": resolved,
        "input_symbol": raw,
        "market": market,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "links": SERENITY_LINKS,
        "source_mode": "public-safe proxy",
        "integration_note": "This panel does not scrape private X sessions or bundle Serenity repo code. It uses public price/news data and a Serenity-style signal framework.",
        "license_note": "GitHub API currently returns no root license metadata for haskaomni/serenity, so this project links to it instead of copying its code.",
        "latest": {
            "date": latest["date_label"],
            "close": number(latest["close"]),
            "change_pct": number((latest["close"] / previous["close"] - 1) * 100) if previous["close"] else 0,
            "volume_ratio": number(latest["VOLUME_RATIO"]),
            "ret20_pct": number(latest["RET20"] * 100),
        },
        "risk": risk,
        "prediction": prediction,
        "news": {"label": news["label"], "score": news["score"], "positive": news["positive"], "negative": news["negative"], "items": news["items"][:5]},
        **score,
    }
    SERENITY_CACHE[cache_key] = (now, payload)
    return payload


@app.get("/api/serenity/recent")
def serenity_recent(
    markets: str = Query("US,TW", max_length=16),
    limit: int = Query(10, ge=3, le=16),
) -> dict[str, Any]:
    selected_markets = {part.strip().upper() for part in markets.split(",") if part.strip()}
    if not selected_markets:
        selected_markets = {"US", "TW"}
    cache_key = (",".join(sorted(selected_markets)), limit)
    now = time.time()
    cached = SERENITY_RECENT_CACHE.get(cache_key)
    if cached and now - cached[0] < 180:
        return cached[1]

    pool = serenity_recent_pool(selected_markets)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(score_serenity_recent_item, item): item for item in pool}
        for future in as_completed(future_map):
            result = future.result()
            if result:
                rows.append(result)
    rows.sort(key=lambda item: (item["serenity_score"], item["confidence"], -item["risk_score"]), reverse=True)
    payload = {
        "ok": True,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "markets": sorted(selected_markets),
        "scanned": len(pool),
        "refresh_seconds": 180,
        "source_mode": "public-safe proxy",
        "note": "Ranks public-data Serenity-style candidates. It does not read private X posts or Capafy chat messages without a user-provided token.",
        "rows": rows[:limit],
        "links": SERENITY_LINKS,
    }
    SERENITY_RECENT_CACHE[cache_key] = (now, payload)
    return payload


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


@app.get("/api/screener/options")
def screener_options() -> dict[str, Any]:
    industries = sorted({item["industry"] for item in STOCK_UNIVERSE})
    return {"markets": ["US", "TW"], "industries": industries, "count": len(STOCK_UNIVERSE)}


@app.get("/api/screener")
def screener(
    markets: str = Query("US,TW", max_length=16),
    industries: str = Query("all", max_length=240),
    min_price: float | None = Query(None, ge=0),
    max_price: float | None = Query(None, ge=0),
    sort_by: str = Query("quality", pattern="^(quality|market_cap|volume|change)$"),
    limit: int = Query(30, ge=1, le=50),
) -> dict[str, Any]:
    selected_markets = {part.strip().upper() for part in markets.split(",") if part.strip()}
    selected_industries = {part.strip() for part in industries.split(",") if part.strip() and part.strip().lower() != "all"}
    pool = [
        item for item in STOCK_UNIVERSE
        if item["market"] in selected_markets and (not selected_industries or item["industry"] in selected_industries)
    ]
    pool = sorted(pool, key=lambda item: item["market_cap_usd"], reverse=True)[:80]

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(screen_one_stock, item): item for item in pool}
        for future in as_completed(future_map):
            result = future.result()
            if not result:
                continue
            price = result["price"]
            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue
            rows.append(result)

    sort_key = {
        "quality": lambda item: item["quality_score"],
        "market_cap": lambda item: item["market_cap_usd"],
        "volume": lambda item: item["volume"],
        "change": lambda item: item["change_pct"],
    }[sort_by]
    rows.sort(key=sort_key, reverse=True)
    return {
        "ok": True,
        "count": len(rows),
        "scanned": len(pool),
        "sort_by": sort_by,
        "rows": rows[:limit],
        "note": "Market cap is an approximate built-in ranking value; price and volume come from Yahoo chart data.",
    }


@app.get("/api/bots")
def bots(
    markets: str = Query("US,TW", max_length=16),
    capital: float = Query(100000, gt=0),
    style: str = Query("all", pattern="^(all|conservative|balanced|aggressive)$"),
    limit: int = Query(5, ge=3, le=8),
) -> dict[str, Any]:
    selected_markets = {part.strip().upper() for part in markets.split(",") if part.strip()}
    pool: list[dict[str, Any]] = []
    for market in sorted(selected_markets):
        market_rows = [item for item in STOCK_UNIVERSE if item["market"] == market]
        pool.extend(sorted(market_rows, key=lambda item: item["market_cap_usd"], reverse=True)[:28])

    candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(screen_one_stock, item): item for item in pool}
        for future in as_completed(future_map):
            result = future.result()
            if result:
                candidates.append(result)
    selected_bots = [bot for bot in BOT_DEFS if style == "all" or bot["style"] == style]
    bot_rows = [simulate_bot(bot, candidates, capital, limit) for bot in selected_bots]
    return {
        "ok": True,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capital": number(capital),
        "markets": sorted(selected_markets),
        "scanned": len(pool),
        "bots": bot_rows,
        "note": "Rule-based paper simulation only. Bots do not trade real money and do not guarantee future returns.",
    }


@app.get("/api/hindsight")
def hindsight(
    symbol: str = Query(..., min_length=1, max_length=32),
    capital: float = Query(100000, gt=0),
    period: str = Query("1y", pattern="^(1mo|3mo|6mo|1y|2y|5y)$"),
    interval: str = Query("1d", pattern="^(1d|1wk)$"),
) -> dict[str, Any]:
    raw = symbol.strip().upper()
    normalized, market = normalize_symbol(raw)
    rows: list[dict[str, Any]] = []
    resolved = normalized
    for candidate in candidate_symbols(normalized, raw):
        rows = fetch_price_history(candidate, period, interval)
        if rows:
            resolved = candidate
            break
    if len(rows) < 5:
        raise HTTPException(status_code=422, detail="Not enough price data for hindsight simulation.")
    payload = hindsight_best_path(rows, capital)
    return {
        "ok": True,
        "symbol": resolved,
        "market": market,
        "period": period,
        "interval": interval,
        "capital": number(capital),
        **payload,
        "warning": "Hindsight mode is for replay and learning. It assumes perfect past knowledge and is not a forward-looking strategy.",
    }


@app.post("/api/profile")
async def create_profile(request: Request) -> dict[str, Any]:
    payload = await safe_json(request)
    code = normalize_profile_code(str(payload.get("code") or ""))
    if not code:
        code = new_profile_code()
    ensure_profile(code)
    return {"ok": True, "code": code, "state": read_profile(code)}


@app.get("/api/profile/{code}")
def get_profile(code: str) -> dict[str, Any]:
    clean = normalize_profile_code(code)
    if not clean:
        raise HTTPException(status_code=422, detail="Invalid profile code.")
    ensure_profile(clean)
    return {"ok": True, "code": clean, "state": read_profile(clean)}


@app.post("/api/profile/{code}")
async def save_profile(code: str, request: Request) -> dict[str, Any]:
    clean = normalize_profile_code(code)
    if not clean:
        raise HTTPException(status_code=422, detail="Invalid profile code.")
    payload = await safe_json(request)
    watch = payload.get("watch", [])
    bots_followed = payload.get("bots", [])
    if not isinstance(watch, list):
        watch = []
    if not isinstance(bots_followed, list):
        bots_followed = []
    ensure_profile(clean)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE profiles SET updated_at=?, watch_json=?, bot_json=? WHERE code=?",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                json.dumps([str(item).upper()[:32] for item in watch[:80]], ensure_ascii=False),
                json.dumps([str(item)[:64] for item in bots_followed[:20]], ensure_ascii=False),
                clean,
            ),
        )
    return {"ok": True, "code": clean, "state": read_profile(clean)}


@app.get("/api/forum")
def get_forum(room_date: str = Query("", max_length=16)) -> dict[str, Any]:
    date_key = room_date if room_date else datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, user_id, text, created_at FROM forum_posts WHERE room_date=? ORDER BY created_at ASC LIMIT 120",
            (date_key,),
        ).fetchall()
    return {
        "ok": True,
        "room_date": date_key,
        "posts": [{"id": row["id"], "user": row["user_id"], "text": row["text"], "time": row["created_at"]} for row in rows],
    }


@app.post("/api/forum")
async def post_forum(request: Request) -> dict[str, Any]:
    payload = await safe_json(request)
    user_id = str(payload.get("user") or "ANON").strip()[:24]
    text = str(payload.get("text") or "").strip()[:1000]
    room_date = str(payload.get("room_date") or datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d"))[:16]
    if not text:
        raise HTTPException(status_code=422, detail="Empty message.")
    post_id = f"{int(datetime.now(timezone.utc).timestamp() * 1000)}-{secrets.token_hex(3)}"
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO forum_posts (id, room_date, user_id, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (post_id, room_date, user_id, text, created_at),
        )
    return {"ok": True, "post": {"id": post_id, "user": user_id, "text": text, "time": created_at}}


@app.delete("/api/forum/{post_id}")
def delete_forum(post_id: str, user: str = Query("", max_length=32)) -> dict[str, Any]:
    with sqlite3.connect(DB_PATH) as conn:
        if user:
            conn.execute("DELETE FROM forum_posts WHERE id=? AND user_id=?", (post_id, user))
        else:
            conn.execute("DELETE FROM forum_posts WHERE id=?", (post_id,))
    return {"ok": True}


@app.get("/api/recommendations")
def recommendations(
    markets: str = Query("US,TW", max_length=16),
    limit: int = Query(8, ge=3, le=12),
) -> dict[str, Any]:
    selected_markets = {part.strip().upper() for part in markets.split(",") if part.strip()}
    pool: list[dict[str, Any]] = []
    for market in sorted(selected_markets):
        market_rows = [item for item in STOCK_UNIVERSE if item["market"] == market]
        pool.extend(sorted(market_rows, key=lambda item: item["market_cap_usd"], reverse=True)[:24])

    screened: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(screen_one_stock, item): item for item in pool}
        for future in as_completed(future_map):
            result = future.result()
            if result:
                screened.append(result)

    screened.sort(key=lambda item: item["quality_score"], reverse=True)
    shortlist = screened[: max(12, limit * 2)]
    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(enrich_recommendation, row): row for row in shortlist}
        for future in as_completed(future_map):
            result = future.result()
            if result:
                enriched.append(result)

    enriched.sort(key=lambda item: item["composite_score"], reverse=True)
    return {
        "ok": True,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanned": len(pool),
        "ranked": len(enriched),
        "rows": enriched[:limit],
        "headlines": fetch_market_headlines(),
        "video_links": finance_video_links(),
        "model": "OLS regression + momentum, technical risk, RSS news sentiment, market-cap/liquidity proxy",
    }


@app.get("/api/movers")
def movers(
    markets: str = Query("US,TW", max_length=16),
    limit: int = Query(8, ge=3, le=12),
    mode: str = Query("recent", pattern="^(recent|live)$"),
) -> dict[str, Any]:
    selected_markets = {part.strip().upper() for part in markets.split(",") if part.strip()}
    cache_key = (",".join(sorted(selected_markets)), limit, mode)
    now_ts = datetime.now(timezone.utc).timestamp()
    ttl = 8 if mode == "live" else 60
    cached = MOVERS_CACHE.get(cache_key)
    if cached and now_ts - cached[0] < ttl:
        return cached[1]

    pool: list[dict[str, Any]] = []
    for market in sorted(selected_markets):
        rows = [item for item in STOCK_UNIVERSE if item["market"] == market]
        pool.extend(sorted(rows, key=lambda item: item["market_cap_usd"], reverse=True)[:24])

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(mover_quote, item, mode): item for item in pool}
        for future in as_completed(future_map):
            result = future.result()
            if result:
                rows.append(result)
    rows.sort(key=lambda item: item["change_pct"], reverse=True)
    payload = {
        "ok": True,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "refresh_seconds": ttl,
        "scanned": len(pool),
        "gainers": rows[:limit],
        "losers": sorted(rows, key=lambda item: item["change_pct"])[:limit],
    }
    MOVERS_CACHE[cache_key] = (now_ts, payload)
    return payload


@app.get("/api/portfolio")
def portfolio(
    amount: float = Query(..., gt=0),
    currency: str = Query("TWD", pattern="^(TWD|USD)$"),
    risk_pct: float = Query(35, ge=0, le=100),
    profile: str = Query("balanced", pattern="^(conservative|balanced|aggressive)$"),
    horizon: str = Query("long", pattern="^(short|long)$"),
    markets: str = Query("US,TW", max_length=16),
    symbols: str = Query("", max_length=256),
    target_pct: float = Query(8, ge=-50, le=300),
) -> dict[str, Any]:
    selected_markets = {part.strip().upper() for part in markets.split(",") if part.strip()}
    selected_symbols = parse_symbol_list(symbols)
    if selected_symbols:
        pool = portfolio_items_from_symbols(selected_symbols, selected_markets)
    else:
        pool = []
        for market in sorted(selected_markets):
            rows = [item for item in STOCK_UNIVERSE if item["market"] == market]
            pool.extend(sorted(rows, key=lambda item: item["market_cap_usd"], reverse=True)[:18])

    candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(portfolio_candidate, item): item for item in pool}
        for future in as_completed(future_map):
            result = future.result()
            if result:
                candidates.append(result)
    min_candidates = 1 if selected_symbols else 3
    if len(candidates) < min_candidates:
        raise HTTPException(status_code=422, detail="Not enough portfolio candidates.")

    scored = [score_portfolio_candidate(row, risk_pct, profile, horizon) for row in candidates]
    if selected_symbols:
        scored = [score_target_candidate(row, target_pct, risk_pct, horizon) for row in scored]
    scored.sort(key=lambda item: item["portfolio_score"], reverse=True)
    chosen = scored if selected_symbols else diversify_candidates(scored, 6 if risk_pct >= 45 else 5)
    allocations = allocate_portfolio(chosen, amount, currency, risk_pct, profile, target_mode=bool(selected_symbols))
    stats = portfolio_statistics(allocations, horizon)
    usd_twd = quote_last("TWD=X").get("price") or 31.8
    rows = build_allocation_rows(allocations, amount, currency, usd_twd)
    warnings = portfolio_warnings(allocations, target_pct, horizon)
    return {
        "ok": True,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": {"amount": number(amount), "currency": currency, "risk_pct": number(risk_pct), "profile": profile, "horizon": horizon, "markets": sorted(selected_markets), "symbols": selected_symbols, "target_pct": number(target_pct)},
        "summary": {
            "cash_weight_pct": number(sum(item["weight"] for item in allocations if item["symbol"] == "CASH") * 100),
            "equity_weight_pct": number(sum(item["weight"] for item in allocations if item["symbol"] != "CASH") * 100),
            "expected_annual_return_pct": number(stats["annual_return"] * 100),
            "annual_volatility_pct": number(stats["annual_volatility"] * 100),
            "sharpe_proxy": number(stats["sharpe_proxy"]),
            "confidence_level_pct": stats["confidence_level_pct"],
        },
        "intervals": stats["intervals"],
        "allocations": rows,
        "warnings": warnings,
        "report": build_portfolio_report(risk_pct, profile, horizon, stats),
        "methodology": "Uses 1-year daily returns, OLS/momentum stock scores, user target return, volatility targeting, max-position caps, cash reserve, concentration checks, and historical-normal confidence intervals. This is research output, not investment advice.",
    }


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
        "related": related_assets(resolved, market),
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


def mover_quote(item: dict[str, Any], mode: str = "recent") -> dict[str, Any] | None:
    if mode == "live":
        rows = fetch_price_history(item["symbol"], "1d", "5m")
        source = "1d/5m"
    else:
        rows = fetch_price_history(item["symbol"], "5d", "1d")
        source = "5d/1d"
    if not rows:
        rows = fetch_price_history(item["symbol"], "5d", "1d")
        source = "5d/1d"
    if len(rows) < 2:
        return None
    latest = rows[-1]
    previous = rows[-2] if mode == "live" else rows[0]
    pattern = detect_price_pattern([row["close"] for row in rows[-12:]])
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "market": item["market"],
        "industry": item["industry"],
        "price": number(latest["close"]),
        "change_pct": number((latest["close"] / previous["close"] - 1) * 100) if previous["close"] else 0,
        "volume": int(latest["volume"] or 0),
        "source": source,
        "mode": mode,
        "pattern": pattern["pattern"],
        "pattern_score": pattern["score"],
        "basis": "last bar" if mode == "live" and source == "1d/5m" else "5d",
    }


def detect_price_pattern(closes: list[float]) -> dict[str, Any]:
    values = [float(value) for value in closes if value]
    if len(values) < 6:
        return {"pattern": "steady", "score": 0}
    first, last = values[0], values[-1]
    low = min(values)
    high = max(values)
    low_index = values.index(low)
    high_index = values.index(high)
    total_range = high - low
    if total_range <= 0:
        return {"pattern": "steady", "score": 0}
    recovery = (last - low) / total_range
    giveback = (high - last) / total_range
    if 1 < low_index < len(values) - 2 and recovery >= 0.62 and last >= first * 0.995:
        return {"pattern": "v_rebound", "score": number(recovery * 100)}
    if 1 < high_index < len(values) - 2 and giveback >= 0.62 and last <= first * 1.005:
        return {"pattern": "inverse_v", "score": number(giveback * 100)}
    if last == high and last > first:
        return {"pattern": "breakout", "score": number((last / first - 1) * 100) if first else 0}
    if last == low and last < first:
        return {"pattern": "selloff", "score": number((last / first - 1) * 100) if first else 0}
    return {"pattern": "steady", "score": 0}


def related_assets(symbol: str, market: str) -> list[dict[str, Any]]:
    key = symbol.upper()
    alias_key = {"TSM": "2330.TW", "2330": "2330.TW"}.get(key, key)
    base_item = find_universe_item(symbol)
    seeds = list(RELATED_ASSETS.get(alias_key, []))
    if not seeds and base_item:
        peers = [
            item for item in STOCK_UNIVERSE
            if item["symbol"] != base_item["symbol"] and item["industry"] == base_item["industry"]
        ]
        for peer in sorted(peers, key=lambda item: item["market_cap_usd"], reverse=True)[:6]:
            seeds.append({
                "symbol": peer["symbol"],
                "name": peer["name"],
                "type": "peer",
                "relation": "same industry peer",
                "structure": "horizontal",
                "why": f"Same {base_item['industry']} industry; useful for checking whether the move is stock-specific or sector-wide.",
            })
    if not seeds:
        return []

    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        future_map = {executor.submit(related_quote, row): row for row in seeds[:8]}
        for future in as_completed(future_map):
            result = future.result()
            if result:
                enriched.append(result)
    order = {row["symbol"]: index for index, row in enumerate(seeds)}
    enriched.sort(key=lambda item: order.get(item["symbol"], 999))
    return enriched


def related_quote(row: dict[str, Any]) -> dict[str, Any] | None:
    quote = quote_last(row["symbol"])
    return {
        **row,
        "price": quote.get("price"),
        "change_pct": quote.get("change_pct"),
        "strength": relation_strength(row["type"], row["structure"]),
    }


def relation_strength(relation_type: str, structure: str) -> str:
    if relation_type in {"leveraged_etf", "inverse_etf", "adr"}:
        return "direct/high"
    if structure == "vertical":
        return "supply-chain/medium"
    if structure == "horizontal":
        return "peer/medium"
    return "sector/medium"


def find_universe_item(symbol: str) -> dict[str, Any] | None:
    plain = symbol.upper()
    candidates = {plain, plain.replace(".TW", "").replace(".TWO", "")}
    for item in STOCK_UNIVERSE:
        if item["symbol"].upper() in candidates or item["symbol"].replace(".TW", "").replace(".TWO", "").upper() in candidates:
            return item
    return None


def screen_one_stock(item: dict[str, Any]) -> dict[str, Any] | None:
    rows = fetch_price_history(item["symbol"], "1y", "1d")
    if len(rows) < 60:
        return None
    rows = calculate_indicators(rows)
    latest, previous = rows[-1], rows[-2]
    levels = support_resistance(rows)
    neutral_news = {"label": "neutral", "positive": 0, "negative": 0, "score": 0, "items": []}
    risk = build_risk(latest, levels, neutral_news)
    suitability = build_suitability(latest, risk, neutral_news)
    prediction = build_prediction(rows, latest, risk, neutral_news)
    quality = screener_quality_score(latest, risk, suitability, prediction, item)
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "market": item["market"],
        "industry": item["industry"],
        "price": number(latest["close"]),
        "change_pct": number((latest["close"] / previous["close"] - 1) * 100) if previous["close"] else 0,
        "volume": int(latest["volume"] or 0),
        "market_cap_usd": int(item["market_cap_usd"]),
        "quality_score": quality["score"],
        "risk_score": risk["score"],
        "trend": risk["trend"],
        "bias": prediction["bias"],
        "intraday_score": suitability["intraday"]["score"],
        "short_score": suitability["short_term"]["score"],
        "long_score": suitability["long_term"]["score"],
        "ret20_pct": number(latest["RET20"] * 100),
        "rsi14": number(latest["RSI14"]),
        "volume_ratio": number(latest["VOLUME_RATIO"]),
        "forecast_20d_pct": prediction["forecast_20d_pct"],
        "confidence": prediction["confidence"],
        "reasons": quality["reasons"],
    }


def enrich_recommendation(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        news = fetch_news(row["symbol"], row["market"])
        technical_score = bounded(100 - row["risk_score"] + (10 if row["trend"] == "bullish" else -8 if row["trend"] == "bearish" else 0))
        statistics_score = bounded(50 + row["forecast_20d_pct"] * 1.8 + row["confidence"] * 0.25 + row["ret20_pct"] * 0.35)
        news_score = bounded(50 + news["score"] * 8 + news["positive"] * 3 - news["negative"] * 4)
        fundamental_score = bounded(
            45
            + (math.log10(max(1000000000, row["market_cap_usd"])) - 9) * 7
            + (math.log10(max(100000, row["volume"])) - 5) * 4
        )
        composite = int(round(statistics_score * 0.32 + technical_score * 0.28 + news_score * 0.22 + fundamental_score * 0.18))
        reasons = []
        if statistics_score >= 65:
            reasons.append("regression/momentum positive")
        if technical_score >= 65:
            reasons.append("technical risk controlled")
        if news_score >= 58:
            reasons.append("news tone supportive")
        if fundamental_score >= 65:
            reasons.append("large/liquid leader")
        if not reasons:
            reasons.append("balanced watch candidate")
        return {
            **row,
            "composite_score": bounded(composite),
            "statistics_score": statistics_score,
            "technical_score": technical_score,
            "news_score": news_score,
            "fundamental_score": fundamental_score,
            "news_label": news["label"],
            "news_positive": news["positive"],
            "news_negative": news["negative"],
            "top_news": news["items"][:2],
            "recommend_reasons": reasons[:4],
        }
    except Exception:
        return None


def build_serenity_signal(
    symbol: str,
    market: str,
    rows: list[dict[str, Any]],
    latest: dict[str, Any],
    previous: dict[str, Any],
    risk: dict[str, Any],
    prediction: dict[str, Any],
    news: dict[str, Any],
) -> dict[str, Any]:
    universe = find_universe_item(symbol) or {"name": symbol, "industry": "Unknown", "market_cap_usd": 0}
    text_blob = serenity_text_blob(symbol, universe, news)
    topics = serenity_topic_hits(symbol, universe, text_blob)
    markers = serenity_marker_hits(text_blob)

    positive_markers = markers["conviction"] + markers["asymmetry"] + markers["supply_chain"] + markers["catalyst"]
    negative_markers = markers["risk"] + markers["caution"]
    theme_score = bounded(34 + len(topics) * 14 + sum(item["hits"] for item in topics) * 4)
    corpus_score = bounded(48 + positive_markers * 7 - negative_markers * 8 + news["score"] * 6 + news["positive"] * 2 - news["negative"] * 3)
    setup_score = serenity_setup_score(latest, previous, risk, prediction)
    risk_score = bounded(int(risk["score"]) + negative_markers * 4 - max(0, markers["asymmetry"] - markers["caution"]) * 2)
    composite = bounded(theme_score * 0.26 + corpus_score * 0.29 + setup_score * 0.28 + (100 - risk_score) * 0.17)
    confidence = serenity_confidence(rows, news, topics, markers, prediction)
    posture = "track" if composite >= 72 and risk_score < 68 else "watch" if composite >= 52 else "avoid"
    drivers, cautions = serenity_driver_lists(topics, markers, latest, risk, prediction, news)

    return {
        "serenity_score": composite,
        "posture": posture,
        "confidence": confidence,
        "scores": {
            "theme": theme_score,
            "corpus": corpus_score,
            "setup": setup_score,
            "risk_adjusted": bounded(100 - risk_score),
        },
        "markers": markers,
        "themes": topics[:5],
        "drivers": drivers[:6],
        "cautions": cautions[:5],
        "tracker_summary": serenity_summary(symbol, composite, posture, risk_score, confidence, topics),
        "methodology": [
            "Map the ticker to Serenity-style supply-chain themes.",
            "Read public RSS headlines for catalyst, asymmetry, conviction, risk and caution markers.",
            "Blend trend, volume, regression momentum and risk score.",
            "Keep X tracking as an external link unless the owner provides a legal public feed or a user imports their own local snapshot.",
        ],
    }


def serenity_recent_pool(selected_markets: set[str]) -> list[dict[str, Any]]:
    explicit = {symbol.upper() for symbol in SERENITY_SYMBOL_THEMES}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in STOCK_UNIVERSE:
        symbol = item["symbol"].upper()
        plain = symbol.replace(".TW", "").replace(".TWO", "")
        thematic = (
            symbol in explicit
            or plain in explicit
            or item["industry"] in {"Semiconductors", "Technology", "Communication", "Energy"}
        )
        if item["market"] in selected_markets and thematic and symbol not in seen:
            rows.append(item)
            seen.add(symbol)
    rows.sort(key=lambda item: (item["symbol"].upper() not in explicit, -int(item["market_cap_usd"])))
    return rows[:32]


def score_serenity_recent_item(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        symbol = item["symbol"]
        rows = fetch_price_history(symbol, "6mo", "1d")
        if len(rows) < 40:
            return None
        rows = calculate_indicators(rows)
        latest, previous = rows[-1], rows[-2]
        levels = support_resistance(rows)
        news = fetch_news(symbol, item["market"])
        risk = build_risk(latest, levels, news)
        prediction = build_prediction(rows, latest, risk, news)
        signal = build_serenity_signal(symbol, item["market"], rows, latest, previous, risk, prediction, news)
        top_news = next((row for row in news["items"] if row.get("link") and "News fetch failed" not in row.get("title", "")), None)
        return {
            "symbol": symbol,
            "name": item["name"],
            "market": item["market"],
            "industry": item["industry"],
            "price": number(latest["close"]),
            "change_pct": number((latest["close"] / previous["close"] - 1) * 100) if previous["close"] else 0,
            "ret20_pct": number(latest["RET20"] * 100),
            "volume_ratio": number(latest["VOLUME_RATIO"]),
            "risk_score": int(risk["score"]),
            "bias": prediction["bias"],
            "serenity_score": signal["serenity_score"],
            "posture": signal["posture"],
            "confidence": signal["confidence"],
            "themes": signal["themes"][:2],
            "drivers": signal["drivers"][:3],
            "cautions": signal["cautions"][:2],
            "news_label": news["label"],
            "top_news": top_news,
        }
    except Exception:
        return None


def serenity_text_blob(symbol: str, universe: dict[str, Any], news: dict[str, Any]) -> str:
    parts = [symbol, universe.get("name", ""), universe.get("industry", "")]
    parts.extend(item.get("title", "") for item in news.get("items", []))
    return " ".join(parts).lower()


def serenity_topic_hits(symbol: str, universe: dict[str, Any], text_blob: str) -> list[dict[str, Any]]:
    explicit = set(SERENITY_SYMBOL_THEMES.get(symbol.upper(), []))
    plain = symbol.upper().replace(".TW", "").replace(".TWO", "")
    explicit.update(SERENITY_SYMBOL_THEMES.get(plain, []))
    industry = str(universe.get("industry") or "").lower()
    if "semi" in industry:
        explicit.add("semi_supply_chain")
    if "technology" in industry:
        explicit.add("ai_infra_neocloud")
    if "energy" in industry:
        explicit.add("power_grid_energy")
    if "financial" in industry or "communication" in industry or "consumer" in industry:
        explicit.add("platform_consumer_fintech")

    hits: list[dict[str, Any]] = []
    for key, info in SERENITY_TOPICS.items():
        keyword_hits = sum(1 for word in info["keywords"] if word in text_blob)
        if key in explicit:
            keyword_hits += 2
        if keyword_hits:
            hits.append({"key": key, "label": info["label"], "hits": int(keyword_hits)})
    hits.sort(key=lambda item: item["hits"], reverse=True)
    return hits


def serenity_marker_hits(text_blob: str) -> dict[str, int]:
    return {key: int(sum(1 for word in words if word in text_blob)) for key, words in SERENITY_MARKERS.items()}


def serenity_setup_score(
    latest: dict[str, Any],
    previous: dict[str, Any],
    risk: dict[str, Any],
    prediction: dict[str, Any],
) -> int:
    change_pct = (latest["close"] / previous["close"] - 1) * 100 if previous["close"] else 0
    trend_bonus = 14 if risk["trend"] == "bullish" else -12 if risk["trend"] == "bearish" else 0
    bias_bonus = 12 if prediction["bias"] == "bullish" else -10 if prediction["bias"] == "bearish" else 0
    ma_bonus = 8 if latest["close"] > latest["MA20"] > latest["MA60"] else -8 if latest["close"] < latest["MA20"] < latest["MA60"] else 0
    volume_bonus = min(12, max(-4, (latest["VOLUME_RATIO"] - 1) * 8))
    momentum = latest["RET20"] * 100 * 0.55 + latest["RET5"] * 100 * 0.35 + change_pct * 0.25
    return bounded(50 + trend_bonus + bias_bonus + ma_bonus + volume_bonus + momentum - int(risk["score"]) * 0.18)


def serenity_confidence(
    rows: list[dict[str, Any]],
    news: dict[str, Any],
    topics: list[dict[str, Any]],
    markers: dict[str, int],
    prediction: dict[str, Any],
) -> int:
    data_depth = min(24, len(rows) / 8)
    news_depth = min(20, len(news.get("items", [])) * 3)
    topic_depth = min(18, len(topics) * 5)
    marker_depth = min(16, sum(markers.values()) * 3)
    model_depth = min(14, int(prediction.get("confidence") or 0) * 0.18)
    return bounded(26 + data_depth + news_depth + topic_depth + marker_depth + model_depth)


def serenity_driver_lists(
    topics: list[dict[str, Any]],
    markers: dict[str, int],
    latest: dict[str, Any],
    risk: dict[str, Any],
    prediction: dict[str, Any],
    news: dict[str, Any],
) -> tuple[list[str], list[str]]:
    drivers: list[str] = []
    cautions: list[str] = []
    if topics:
        drivers.append(f"Theme fit: {', '.join(item['label'] for item in topics[:2])}.")
    if markers["conviction"]:
        drivers.append("Public headlines contain conviction/upside markers.")
    if markers["asymmetry"]:
        drivers.append("Asymmetry or rerating language appears in the public corpus.")
    if markers["supply_chain"]:
        drivers.append("Supply-chain or bottleneck language is present.")
    if markers["catalyst"]:
        drivers.append("Catalyst markers such as earnings, guidance, orders or launches appear.")
    if risk["trend"] == "bullish":
        drivers.append("Price trend is above key moving-average support.")
    if prediction["bias"] == "bullish":
        drivers.append("OLS/momentum model leans bullish.")
    if news["label"] == "positive":
        drivers.append("RSS sentiment leans positive.")

    if markers["risk"]:
        cautions.append("Risk keywords appear in the public corpus.")
    if markers["caution"]:
        cautions.append("Caution/overheated language appears in the public corpus.")
    if int(risk["score"]) >= 70:
        cautions.append("Technical risk score is high.")
    elif int(risk["score"]) >= 55:
        cautions.append("Technical risk is medium; avoid over-sizing.")
    if latest["RSI14"] >= 75:
        cautions.append("RSI is overheated; pullback risk is elevated.")
    if latest["VOLUME_RATIO"] >= 1.8:
        cautions.append("Volume is unusually high; move may be event-driven.")
    if news["label"] == "negative":
        cautions.append("RSS sentiment leans negative.")
    if not drivers:
        drivers.append("No strong Serenity-style theme found; treat as a neutral watch.")
    if not cautions:
        cautions.append("No severe public-data warning found, but this is not a trading signal.")
    return drivers, cautions


def serenity_summary(
    symbol: str,
    score: int,
    posture: str,
    risk_score: int,
    confidence: int,
    topics: list[dict[str, Any]],
) -> str:
    top_theme = topics[0]["label"] if topics else "no dominant theme"
    posture_text = {"track": "track actively", "watch": "watch with conditions", "avoid": "avoid or wait"}[posture]
    return f"{symbol}: Serenity-style score {score}/100, {posture_text}. Top theme: {top_theme}. Risk {risk_score}/100, confidence {confidence}/100."


def fetch_market_headlines() -> list[dict[str, Any]]:
    queries = [
        ("TW Finance", "台股 財經 半導體 匯率 投資"),
        ("US Finance", "US stocks earnings Fed rates market"),
    ]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source, query in queries:
        if source.startswith("TW"):
            url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        else:
            url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
        for item in fetch_feed(url, source):
            title = item["title"].strip()
            key = title.lower()
            if title and key not in seen and "News fetch failed" not in title:
                seen.add(key)
                items.append({**item, "topic": source})
    return items[:12]


def finance_video_links() -> list[dict[str, str]]:
    return [
        {"title": "YouTube 台股財經直播搜尋", "source": "YouTube", "link": "https://www.youtube.com/results?search_query=%E5%8F%B0%E8%82%A1+%E8%B2%A1%E7%B6%93+%E7%9B%B4%E6%92%AD"},
        {"title": "YouTube 美股財經直播搜尋", "source": "YouTube", "link": "https://www.youtube.com/results?search_query=%E7%BE%8E%E8%82%A1+%E8%B2%A1%E7%B6%93+%E7%9B%B4%E6%92%AD"},
        {"title": "YouTube Stock Market Live", "source": "YouTube", "link": "https://www.youtube.com/results?search_query=stock+market+live"},
    ]


MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def fetch_macro_event_alerts(days: int) -> list[dict[str, Any]]:
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    horizon = today + timedelta(days=days)
    events: list[dict[str, Any]] = []
    events.extend(fetch_fomc_events(today, horizon))
    events.extend(fetch_bea_events(today, horizon))
    return dedupe_events(events)


def fetch_fomc_events(today: date, horizon: date) -> list[dict[str, Any]]:
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    try:
        text = requests.get(url, headers={"User-Agent": "Mozilla/5.0 StockRiskRadar/4.5"}, timeout=10).text
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    for year in {today.year, horizon.year, today.year + 1}:
        marker = re.search(rf'<a id="[^"]+">{year} FOMC Meetings</a>', text)
        if not marker:
            continue
        next_panel = text.find('<div class="panel panel-default"><div class="panel-heading"><h4><a id=', marker.end())
        section = text[marker.end() : next_panel if next_panel != -1 else len(text)]
        row_pattern = re.compile(
            r'<strong>(January|February|March|April|May|June|July|August|September|October|November|December)</strong>.*?'
            r'fomc-meeting__date[^>]*>([^<]+)</div>',
            re.S | re.I,
        )
        for match in row_pattern.finditer(section):
            month_name = match.group(1)
            date_text = re.sub(r"<.*?>", "", match.group(2)).strip()
            day_match = re.search(r"\d{1,2}", date_text)
            if not day_match:
                continue
            event_date = date(year, MONTHS[month_name.lower()], int(day_match.group(0)))
            if today <= event_date <= horizon:
                has_sep = "*" in date_text
                events.append(
                    event_payload(
                        event_date,
                        "macro",
                        "Fed FOMC meeting / rate decision",
                        "Federal Reserve",
                        url,
                        "high",
                        95,
                        f"{month_name} {date_text}, {year}" + ("; includes Summary of Economic Projections" if has_sep else ""),
                    )
                )
    return events


def fetch_bea_events(today: date, horizon: date) -> list[dict[str, Any]]:
    url = "https://www.bea.gov/news/schedule"
    try:
        text = requests.get(url, headers={"User-Agent": "Mozilla/5.0 StockRiskRadar/4.5"}, timeout=10).text
    except Exception:
        return []
    rows = re.findall(
        r'<td class="scheduled-date[^"]*"[^>]*><div class="release-date">([^<]+)</div>\s*<small[^>]*>([^<]+)</small>.*?'
        r'<td class="release-title[^"]*"[^>]*>(.*?)</td>',
        text,
        re.S | re.I,
    )
    events: list[dict[str, Any]] = []
    important_terms = ("gross domestic product", "personal income and outlays", "pce", "corporate profits", "international trade")
    for date_text, time_text, title_html in rows:
        title = clean_html(title_html)
        title_l = title.lower()
        if not any(term in title_l for term in important_terms):
            continue
        for year in {today.year, horizon.year}:
            event_date = parse_month_day(date_text, year)
            if event_date and today <= event_date <= horizon:
                score = 88 if "gross domestic product" in title_l or "personal income" in title_l else 76
                events.append(event_payload(event_date, "macro", title, "BEA", url, "high" if score >= 85 else "medium", score, f"{date_text} {time_text} ET"))
                break
    return events[:8]


def fetch_earnings_alerts(symbol: str, market: str, days: int) -> list[dict[str, Any]]:
    if not symbol:
        return []
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    horizon = today + timedelta(days=days)
    events: list[dict[str, Any]] = []
    for candidate in candidate_symbols(symbol, symbol):
        data = yahoo_quote_summary(candidate, "calendarEvents")
        earnings = (((data.get("calendarEvents") or {}).get("earnings") or {}) if data else {})
        dates = earnings.get("earningsDate") or []
        if not isinstance(dates, list):
            dates = [dates]
        for item in dates:
            event_date = yahoo_raw_date(item)
            if event_date and today <= event_date <= horizon:
                estimate = bool(earnings.get("isEarningsDateEstimate"))
                eps = (earnings.get("earningsAverage") or {}).get("fmt")
                revenue = (earnings.get("revenueAverage") or {}).get("fmt")
                note_bits = ["estimated date" if estimate else "confirmed/official calendar date"]
                if eps:
                    note_bits.append(f"EPS avg {eps}")
                if revenue:
                    note_bits.append(f"Revenue avg {revenue}")
                events.append(
                    event_payload(
                        event_date,
                        "earnings",
                        f"{candidate} earnings / financial report",
                        "Yahoo Finance calendarEvents",
                        f"https://finance.yahoo.com/quote/{candidate}/analysis",
                        "high",
                        90 if not estimate else 82,
                        "; ".join(note_bits),
                        symbol=candidate,
                        market=market,
                    )
                )
        if events:
            break
    if events:
        return events[:3]
    return earnings_news_fallback(symbol, market, today, horizon)


def yahoo_quote_summary(symbol: str, modules: str) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 StockRiskRadar/4.5"})
    try:
        session.get("https://fc.yahoo.com", timeout=8)
        crumb = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=8).text.strip()
        resp = session.get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}",
            params={"modules": modules, "crumb": crumb},
            timeout=10,
        )
        resp.raise_for_status()
        return ((resp.json().get("quoteSummary") or {}).get("result") or [{}])[0] or {}
    except Exception:
        return {}


def fetch_event_news_watch(symbol: str, market: str, days: int) -> list[dict[str, Any]]:
    queries = [
        ("Macro Watch", "Fed rate decision FOMC PCE GDP central bank announcement market date"),
        ("TW Central Bank", "台灣 央行 理監事會 利率 決議 日期 股市 匯率"),
    ]
    if symbol:
        plain = symbol.replace(".TW", "").replace(".TWO", "")
        if market == "TW":
            queries.append(("Earnings Watch", f"{plain} 財報 法說會 除息 日期 公布"))
        else:
            queries.append(("Earnings Watch", f"{symbol} earnings date guidance report"))
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    cutoff = today - timedelta(days=5)
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source, query in queries:
        is_tw = source == "TW Central Bank" or market == "TW" and source == "Earnings Watch"
        url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={'zh-TW' if is_tw else 'en-US'}&gl={'TW' if is_tw else 'US'}&ceid={'TW:zh-Hant' if is_tw else 'US:en'}"
        for item in fetch_feed(url, source)[:5]:
            if "News fetch failed" in item["title"]:
                continue
            published = parse_feed_date(item.get("published", ""))
            if published and published < cutoff:
                continue
            key = item["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            events.append(
                event_payload(
                    published or today,
                    "news_watch",
                    item["title"],
                    item["source"],
                    item.get("link", ""),
                    "medium",
                    58 + min(12, abs(int(item.get("sentiment", 0))) * 3),
                    "News-based reminder; open the source to confirm exact announcement date.",
                    symbol=symbol if source == "Earnings Watch" else "",
                    market=market if source == "Earnings Watch" else "",
                )
            )
    return dedupe_events(events)[:6]


def earnings_news_fallback(symbol: str, market: str, today: date, horizon: date) -> list[dict[str, Any]]:
    plain = symbol.replace(".TW", "").replace(".TWO", "")
    query = f"{plain} 財報 法說會 日期" if market == "TW" else f"{symbol} earnings date"
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl={'zh-TW' if market == 'TW' else 'en-US'}&gl={'TW' if market == 'TW' else 'US'}&ceid={'TW:zh-Hant' if market == 'TW' else 'US:en'}"
    rows = []
    for item in fetch_feed(url, "Google News")[:3]:
        if "News fetch failed" in item["title"]:
            continue
        published = parse_feed_date(item.get("published", "")) or today
        if today - timedelta(days=10) <= published <= horizon:
            rows.append(event_payload(published, "earnings", item["title"], item["source"], item.get("link", ""), "medium", 62, "No structured earnings date found; this is a news fallback.", symbol=symbol, market=market))
    return rows[:2]


def event_payload(
    event_date: date,
    category: str,
    title: str,
    source: str,
    link: str,
    importance: str,
    score: int,
    note: str,
    symbol: str = "",
    market: str = "",
) -> dict[str, Any]:
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    return {
        "date": event_date.isoformat(),
        "days_left": (event_date - today).days,
        "category": category,
        "title": title.strip(),
        "source": source,
        "link": link,
        "importance": importance,
        "score": int(score),
        "note": note,
        "symbol": symbol,
        "market": market,
    }


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    rows: list[dict[str, Any]] = []
    for item in sorted(events, key=lambda row: (row.get("date") or "9999-12-31", -int(row.get("score", 0)))):
        key = (item.get("date", ""), item.get("category", ""), item.get("title", "").lower()[:90])
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def parse_month_day(value: str, year: int) -> date | None:
    parts = value.strip().split()
    if len(parts) < 2:
        return None
    month = MONTHS.get(parts[0].lower())
    day_match = re.search(r"\d{1,2}", parts[1])
    if not month or not day_match:
        return None
    try:
        return date(year, month, int(day_match.group(0)))
    except ValueError:
        return None


def yahoo_raw_date(item: Any) -> date | None:
    if not isinstance(item, dict):
        return None
    raw = item.get("raw")
    if raw:
        try:
            return datetime.fromtimestamp(int(raw), timezone.utc).date()
        except (TypeError, ValueError, OSError):
            pass
    fmt = item.get("fmt")
    if fmt:
        try:
            return datetime.strptime(fmt, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def parse_feed_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(ZoneInfo("Asia/Taipei")).date()
    except Exception:
        return None


def clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<.*?>", "", value or "")).strip()


def screener_quality_score(
    latest: dict[str, Any],
    risk: dict[str, Any],
    suitability: dict[str, Any],
    prediction: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    suitability_avg = (
        suitability["intraday"]["score"] + suitability["short_term"]["score"] + suitability["long_term"]["score"]
    ) / 3
    score = 50
    score += (100 - int(risk["score"])) * 0.25
    score += suitability_avg * 0.25
    score += 8 if risk["trend"] == "bullish" else -8 if risk["trend"] == "bearish" else 0
    score += 7 if prediction["bias"] == "bullish" else -7 if prediction["bias"] == "bearish" else 0
    score += min(8, math.log10(max(10, latest["volume"])) - 4)
    score += min(7, math.log10(max(1000000000, int(item["market_cap_usd"]))) - 9)
    if latest["RSI14"] >= 78:
        score -= 8
    elif 45 <= latest["RSI14"] <= 68:
        score += 4
    if latest["close"] > latest["MA20"] > latest["MA60"]:
        score += 7
    elif latest["close"] < latest["MA20"] < latest["MA60"]:
        score -= 9
    score = int(max(0, min(100, round(score))))

    reasons = []
    if risk["trend"] == "bullish":
        reasons.append("trend above key averages")
    if prediction["bias"] == "bullish":
        reasons.append("model bias bullish")
    if int(risk["score"]) < 45:
        reasons.append("risk score controlled")
    if latest["VOLUME_RATIO"] >= 1.2:
        reasons.append("volume above average")
    if not reasons:
        reasons.append("balanced but not high-conviction")
    return {"score": score, "reasons": reasons[:3]}


async def safe_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def new_profile_code() -> str:
    return f"SRR-{secrets.token_hex(3).upper()}"


def normalize_profile_code(code: str) -> str:
    clean = "".join(ch for ch in code.strip().upper() if ch.isalnum() or ch == "-")
    return clean[:20]


def ensure_profile(code: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO profiles (code, created_at, updated_at, watch_json, bot_json) VALUES (?, ?, ?, '[]', '[]')",
            (code, now, now),
        )


def read_profile(code: str) -> dict[str, Any]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT code, created_at, updated_at, watch_json, bot_json FROM profiles WHERE code=?", (code,)).fetchone()
    if not row:
        return {"watch": [], "bots": []}
    try:
        watch = json.loads(row["watch_json"] or "[]")
    except Exception:
        watch = []
    try:
        bots_followed = json.loads(row["bot_json"] or "[]")
    except Exception:
        bots_followed = []
    return {"watch": watch, "bots": bots_followed, "updated_at": row["updated_at"], "created_at": row["created_at"]}


def simulate_bot(bot: dict[str, Any], candidates: list[dict[str, Any]], capital: float, limit: int) -> dict[str, Any]:
    scored = [score_bot_candidate(bot, row) for row in candidates]
    scored.sort(key=lambda item: item["bot_score"], reverse=True)
    picks = diversify_bot_picks(scored, bot, limit)
    weights = bot_weights(picks, bot)
    rows: list[dict[str, Any]] = []
    for pick, weight in zip(picks, weights):
        amount = capital * weight
        rows.append({
            "symbol": pick["symbol"],
            "name": pick["name"],
            "market": pick["market"],
            "industry": pick["industry"],
            "weight_pct": number(weight * 100),
            "amount": number(amount),
            "price": pick["price"],
            "shares": int(amount // pick["price"]) if pick["price"] else 0,
            "score": pick["bot_score"],
            "change_pct": pick["change_pct"],
            "ret20_pct": pick.get("ret20_pct"),
            "risk_score": pick["risk_score"],
            "reasons": pick["bot_reasons"],
        })
    expected = sum((row.get("ret20_pct") or row.get("change_pct") or 0) * (row["weight_pct"] / 100) for row in rows)
    risk = number(avg([row["risk_score"] for row in rows]) if rows else bot["risk"])
    return {
        **bot,
        "capital": number(capital),
        "expected_20d_pct": number(expected),
        "paper_pnl_20d": number(capital * expected / 100),
        "avg_risk_score": risk,
        "picks": rows,
        "warnings": bot_warnings(bot, rows),
    }


def score_bot_candidate(bot: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    score = row["quality_score"] * 0.38 + (100 - row["risk_score"]) * 0.18 + row["change_pct"] * 1.4 + row.get("ret20_pct", 0) * 0.65
    reasons = list(row.get("reasons", []))[:2]
    bot_id = bot["id"]
    if bot_id == "steady_turtle":
        score += row["long_score"] * 0.35 - max(0, row["risk_score"] - 42) * 0.9
        reasons.append("trend with low-risk filter")
    elif bot_id == "value_guard":
        score += math.log10(max(10_000_000, row["market_cap_usd"])) * 3 - max(0, row["risk_score"] - 50) * 0.55
        reasons.append("large-cap quality bias")
    elif bot_id == "balanced_compass":
        score += row["short_score"] * 0.18 + row["long_score"] * 0.20
        reasons.append("balanced short/long suitability")
    elif bot_id == "rocket_breakout":
        score += max(0, row["change_pct"]) * 6 + max(0, row.get("ret20_pct", 0)) * 1.2 - max(0, 55 - row["volume"] / 1_000_000)
        reasons.append("momentum/breakout bias")
    elif bot_id == "dip_reversal":
        pullback_bonus = 14 if -8 <= row.get("ret20_pct", 0) <= 3 and row["trend"] != "bearish" else 0
        score += pullback_bonus + row["short_score"] * 0.22
        reasons.append("pullback recovery setup")
    elif bot_id == "chip_hunter":
        score += 18 if row["industry"] in {"Semiconductors", "Technology"} else -10
        score += max(0, row.get("ret20_pct", 0)) * 1.1
        reasons.append("AI/semiconductor concentration")
    return {**row, "bot_score": bounded(score), "bot_reasons": reasons[:4]}


def diversify_bot_picks(rows: list[dict[str, Any]], bot: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    industry_limit = 3 if bot["id"] in {"chip_hunter", "rocket_breakout"} else 2
    industry_counts: dict[str, int] = {}
    for row in rows:
        if industry_counts.get(row["industry"], 0) >= industry_limit:
            continue
        picks.append(row)
        industry_counts[row["industry"]] = industry_counts.get(row["industry"], 0) + 1
        if len(picks) >= limit:
            break
    return picks or rows[:limit]


def bot_weights(picks: list[dict[str, Any]], bot: dict[str, Any]) -> list[float]:
    if not picks:
        return []
    cap = 0.34 if bot["style"] == "aggressive" else 0.26 if bot["style"] == "balanced" else 0.2
    raw = [max(8, row["bot_score"]) for row in picks]
    total = sum(raw) or 1
    weights = [min(cap, score / total) for score in raw]
    leftover = max(0, 1 - sum(weights))
    if leftover:
        room = [max(0, cap - weight) for weight in weights]
        room_total = sum(room)
        if room_total:
            weights = [weight + leftover * room[i] / room_total for i, weight in enumerate(weights)]
    total = sum(weights) or 1
    return [weight / total for weight in weights]


def bot_warnings(bot: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if bot["style"] == "aggressive":
        warnings.append("Aggressive bot: momentum can reverse quickly; use smaller capital or tighter review cadence.")
    industry_weights: dict[str, float] = {}
    for row in rows:
        industry_weights[row["industry"]] = industry_weights.get(row["industry"], 0) + row["weight_pct"]
    for industry, weight in industry_weights.items():
        if weight >= 48:
            warnings.append(f"Concentration warning: {industry} is {number(weight)}% of this bot basket.")
            break
    if not warnings:
        warnings.append("No severe concentration warning in this bot basket, but this remains paper simulation.")
    return warnings[:3]


def hindsight_best_path(rows: list[dict[str, Any]], capital: float) -> dict[str, Any]:
    value = capital
    trades: list[dict[str, Any]] = []
    best_single = {"profit": -10**18, "buy": None, "sell": None}
    min_price = rows[0]["close"]
    min_date = rows[0]["date_label"]
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if prev["close"] and cur["close"] > prev["close"]:
            shares = int(value // prev["close"])
            if shares > 0:
                profit = shares * (cur["close"] - prev["close"])
                value += profit
                trades.append({
                    "buy_date": prev["date_label"],
                    "sell_date": cur["date_label"],
                    "buy": number(prev["close"]),
                    "sell": number(cur["close"]),
                    "shares": shares,
                    "profit": number(profit),
                    "return_pct": number((cur["close"] / prev["close"] - 1) * 100),
                })
        if cur["close"] - min_price > best_single["profit"]:
            best_single = {"profit": cur["close"] - min_price, "buy": min_date, "sell": cur["date_label"], "buy_price": min_price, "sell_price": cur["close"]}
        if cur["close"] < min_price:
            min_price = cur["close"]
            min_date = cur["date_label"]
    top_trades = sorted(trades, key=lambda item: item["profit"], reverse=True)[:8]
    return {
        "start_price": number(rows[0]["close"]),
        "end_price": number(rows[-1]["close"]),
        "final_value": number(value),
        "max_profit": number(value - capital),
        "max_return_pct": number((value / capital - 1) * 100),
        "trade_count": len(trades),
        "top_trades": top_trades,
        "best_single_trade": {
            "buy_date": best_single.get("buy"),
            "sell_date": best_single.get("sell"),
            "buy": number(best_single.get("buy_price")),
            "sell": number(best_single.get("sell_price")),
            "profit_per_share": number(best_single.get("profit")),
        },
    }


def parse_symbol_list(symbols: str) -> list[str]:
    cleaned: list[str] = []
    for part in symbols.replace(";", ",").replace(" ", ",").split(","):
        raw = part.strip().upper()
        if not raw or raw in cleaned:
            continue
        cleaned.append(raw)
        if len(cleaned) >= 12:
            break
    return cleaned


def portfolio_items_from_symbols(symbols: list[str], selected_markets: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in symbols:
        normalized, market = normalize_symbol(raw)
        if selected_markets and market not in selected_markets:
            continue
        found = find_universe_item(normalized) or find_universe_item(raw)
        if found:
            rows.append(found)
            continue
        resolved = normalized
        for candidate in candidate_symbols(normalized, raw):
            if fetch_price_history(candidate, "5d", "1d"):
                resolved = candidate
                break
        rows.append({
            "symbol": resolved,
            "name": resolved,
            "market": market,
            "industry": "Custom",
            "market_cap_usd": 1_000_000_000,
        })
    return rows


def portfolio_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    rows = fetch_price_history(item["symbol"], "1y", "1d")
    if len(rows) < 80:
        return None
    rows = calculate_indicators(rows)
    latest, previous = rows[-1], rows[-2]
    neutral_news = {"label": "neutral", "positive": 0, "negative": 0, "score": 0, "items": []}
    levels = support_resistance(rows)
    risk = build_risk(latest, levels, neutral_news)
    suitability = build_suitability(latest, risk, neutral_news)
    prediction = build_prediction(rows, latest, risk, neutral_news)
    quality = screener_quality_score(latest, risk, suitability, prediction, item)
    closes = [row["close"] for row in rows if row["close"]]
    returns = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]
    annual_return = avg(returns[-252:]) * 252 if returns else 0
    annual_volatility = stddev(returns[-252:]) * math.sqrt(252) if len(returns) > 2 else 0
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "market": item["market"],
        "industry": item["industry"],
        "price": number(latest["close"]),
        "change_pct": number((latest["close"] / previous["close"] - 1) * 100) if previous["close"] else 0,
        "market_cap_usd": int(item["market_cap_usd"]),
        "volume": int(latest["volume"] or 0),
        "quality_score": quality["score"],
        "risk_score": risk["score"],
        "trend": risk["trend"],
        "bias": prediction["bias"],
        "short_score": suitability["short_term"]["score"],
        "long_score": suitability["long_term"]["score"],
        "forecast_20d_pct": prediction["forecast_20d_pct"],
        "confidence": prediction["confidence"],
        "ret20_pct": number(latest["RET20"] * 100),
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "daily_returns": returns[-252:],
        "reasons": quality["reasons"],
    }


def score_portfolio_candidate(row: dict[str, Any], risk_pct: float, profile: str, horizon: str) -> dict[str, Any]:
    horizon_score = row["short_score"] if horizon == "short" else row["long_score"]
    trend_bonus = 6 if row["trend"] == "bullish" else -7 if row["trend"] == "bearish" else 0
    bias_bonus = 6 if row["bias"] == "bullish" else -5 if row["bias"] == "bearish" else 0
    vol_pct = row["annual_volatility"] * 100
    vol_target = 12 + risk_pct * 0.28
    vol_penalty = max(0, vol_pct - vol_target) * (0.45 if profile == "conservative" else 0.28 if profile == "balanced" else 0.16)
    upside = row["forecast_20d_pct"] * (0.7 if horizon == "short" else 0.35)
    score = (
        row["quality_score"] * 0.34
        + horizon_score * 0.24
        + (100 - row["risk_score"]) * 0.16
        + row["confidence"] * 0.10
        + trend_bonus
        + bias_bonus
        + upside
        - vol_penalty
    )
    if profile == "aggressive":
        score += max(0, row["annual_return"] * 100) * 0.06
    if profile == "conservative":
        score -= max(0, row["risk_score"] - 45) * 0.12
    return {**row, "portfolio_score": bounded(score)}


def score_target_candidate(row: dict[str, Any], target_pct: float, risk_pct: float, horizon: str) -> dict[str, Any]:
    expected_pct = row["forecast_20d_pct"] if horizon == "short" else row["annual_return"] * 100
    vol_pct = max(1.0, row["annual_volatility"] * 100)
    target_gap = abs(expected_pct - target_pct)
    reward = expected_pct * 1.25 + max(0, target_pct - target_gap) * 0.55
    risk_penalty = vol_pct * (0.42 - min(0.26, risk_pct / 400))
    concentration_bonus = 5 if row["industry"] not in {"Semiconductors", "Technology"} else 0
    score = row["portfolio_score"] * 0.45 + reward - risk_penalty + concentration_bonus
    return {
        **row,
        "portfolio_score": bounded(score),
        "target_expected_pct": number(expected_pct),
        "target_gap_pct": number(target_gap),
    }


def diversify_candidates(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    industry_counts: dict[str, int] = {}
    market_counts: dict[str, int] = {}
    for row in rows:
        if industry_counts.get(row["industry"], 0) >= 2:
            continue
        if market_counts.get(row["market"], 0) >= max(3, limit - 1):
            continue
        chosen.append(row)
        industry_counts[row["industry"]] = industry_counts.get(row["industry"], 0) + 1
        market_counts[row["market"]] = market_counts.get(row["market"], 0) + 1
        if len(chosen) >= limit:
            break
    if len(chosen) < min(3, limit):
        for row in rows:
            if row not in chosen:
                chosen.append(row)
            if len(chosen) >= limit:
                break
    return chosen


def cash_weight(risk_pct: float, profile: str) -> float:
    base = 0.34 if profile == "conservative" else 0.22 if profile == "balanced" else 0.10
    adjustment = (50 - risk_pct) / 100 * 0.22
    return max(0.04, min(0.55, base + adjustment))


def allocate_portfolio(
    chosen: list[dict[str, Any]],
    amount: float,
    currency: str,
    risk_pct: float,
    profile: str,
    target_mode: bool = False,
) -> list[dict[str, Any]]:
    if not chosen:
        return [{"symbol": "CASH", "name": "Cash reserve", "market": currency, "industry": "Cash", "weight": 1.0}]
    cash = cash_weight(risk_pct, profile)
    cap = 0.30 if target_mode else 0.22 if profile == "conservative" else 0.28 if profile == "balanced" else 0.35
    if target_mode and profile == "aggressive":
        cap = 0.38
    elif target_mode and profile == "conservative":
        cap = 0.24
    equity_budget = 1 - cash
    raw_scores = [max(5, row["portfolio_score"]) for row in chosen]
    total_score = sum(raw_scores) or 1
    rows: list[dict[str, Any]] = []
    capped_extra = 0.0
    for row, score in zip(chosen, raw_scores):
        weight = equity_budget * score / total_score
        if weight > cap:
            capped_extra += weight - cap
            weight = cap
        rows.append({**row, "weight": weight})
    room_rows = [row for row in rows if row["weight"] < cap]
    room_total = sum(cap - row["weight"] for row in room_rows)
    if capped_extra and room_total:
        for row in room_rows:
            row["weight"] += capped_extra * ((cap - row["weight"]) / room_total)
    used = sum(row["weight"] for row in rows)
    rows.append({"symbol": "CASH", "name": "Cash reserve", "market": currency, "industry": "Cash", "weight": max(0, 1 - used)})
    return sorted(rows, key=lambda item: item["weight"], reverse=True)


def portfolio_statistics(allocations: list[dict[str, Any]], horizon: str) -> dict[str, Any]:
    assets = [item for item in allocations if item.get("symbol") != "CASH" and item.get("daily_returns")]
    min_len = min((len(item["daily_returns"]) for item in assets), default=0)
    if min_len >= 20:
        daily = []
        for i in range(-min_len, 0):
            daily.append(sum(item["daily_returns"][i] * item["weight"] for item in assets))
        annual_return = avg(daily) * 252
        annual_volatility = stddev(daily) * math.sqrt(252)
        sample_size = len(daily)
    else:
        annual_return = sum(item.get("annual_return", 0) * item["weight"] for item in assets)
        annual_volatility = math.sqrt(sum((item.get("annual_volatility", 0) * item["weight"]) ** 2 for item in assets))
        sample_size = 0
    confidence_level = bounded(55 + min(20, math.sqrt(max(1, sample_size)) * 1.8) + min(12, len(assets) * 2), 55, 90)
    return {
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe_proxy": annual_return / annual_volatility if annual_volatility else 0,
        "confidence_level_pct": confidence_level,
        "intervals": {
            "selected": interval_payload(annual_return, annual_volatility, 20 if horizon == "short" else 252),
            "short_20d": interval_payload(annual_return, annual_volatility, 20),
            "long_252d": interval_payload(annual_return, annual_volatility, 252),
        },
        "sample_size": sample_size,
    }


def interval_payload(annual_return: float, annual_volatility: float, days: int) -> dict[str, Any]:
    mean_return = annual_return * days / 252
    sigma = annual_volatility * math.sqrt(days / 252)
    return {
        "days": days,
        "expected_return_pct": number(mean_return * 100),
        "ci80_low_pct": number((mean_return - 1.2816 * sigma) * 100),
        "ci80_high_pct": number((mean_return + 1.2816 * sigma) * 100),
        "ci95_low_pct": number((mean_return - 1.96 * sigma) * 100),
        "ci95_high_pct": number((mean_return + 1.96 * sigma) * 100),
        "downside_95_pct": number((mean_return - 1.645 * sigma) * 100),
    }


def portfolio_warnings(allocations: list[dict[str, Any]], target_pct: float, horizon: str) -> list[str]:
    assets = [item for item in allocations if item.get("symbol") != "CASH"]
    warnings: list[str] = []
    if not assets:
        return warnings
    industry_weights: dict[str, float] = {}
    market_weights: dict[str, float] = {}
    memory_symbols = {"MU", "MUU", "WDC", "STX", "6488.TW", "6488.TWO", "2408.TW"}
    memory_weight = 0.0
    for item in assets:
        weight = item.get("weight", 0)
        industry_weights[item.get("industry", "Unknown")] = industry_weights.get(item.get("industry", "Unknown"), 0) + weight
        market_weights[item.get("market", "Unknown")] = market_weights.get(item.get("market", "Unknown"), 0) + weight
        if item.get("symbol") in memory_symbols:
            memory_weight += weight
    top_asset = max(assets, key=lambda item: item.get("weight", 0))
    if top_asset.get("weight", 0) >= 0.34:
        warnings.append(f"Single-position concentration: {top_asset['symbol']} is {number(top_asset['weight'] * 100)}% of capital. A gap move can dominate results.")
    for industry, weight in sorted(industry_weights.items(), key=lambda pair: pair[1], reverse=True):
        if weight >= 0.5:
            warnings.append(f"Industry concentration: {industry} is {number(weight * 100)}%. Consider adding non-correlated sectors or keeping more cash.")
            break
    if memory_weight >= 0.4:
        warnings.append(f"Memory/semiconductor cycle warning: memory-linked names are {number(memory_weight * 100)}%. Earnings, DRAM/NAND pricing and AI capex news can move them together.")
    if len(market_weights) == 1 and len(assets) >= 3:
        market = next(iter(market_weights))
        warnings.append(f"Market concentration: all selected equities are {market}. FX, index and local liquidity shocks are not hedged.")
    if target_pct >= (18 if horizon == "short" else 35):
        warnings.append("Return target is aggressive for the selected horizon. The optimizer will tilt toward volatile names, so downside confidence intervals matter more than the headline expected return.")
    if not warnings:
        warnings.append("No severe concentration warning detected. Still review earnings dates, liquidity, FX and overnight gap risk before acting.")
    warnings.append("Hedge idea: if the basket is semiconductor-heavy, compare SMH/SOXX for sector confirmation and consider cash or inverse/low-beta hedges instead of adding more correlated chip names.")
    return warnings[:5]


def build_allocation_rows(
    allocations: list[dict[str, Any]],
    amount: float,
    currency: str,
    usd_twd: float,
) -> list[dict[str, Any]]:
    rows = []
    rate = usd_twd or 31.8
    for item in allocations:
        input_amount = amount * item["weight"]
        if item["symbol"] == "CASH":
            rows.append({
                "symbol": "CASH",
                "name": "Cash reserve",
                "market": currency,
                "industry": "Cash",
                "weight_pct": number(item["weight"] * 100),
                "amount": number(input_amount),
                "currency": currency,
                "shares": 0,
                "price": 1,
                "score": None,
                "reasons": ["cash buffer for volatility and entry flexibility"],
            })
            continue
        local_currency = "TWD" if item["market"] == "TW" else "USD"
        local_amount = input_amount
        if currency == "TWD" and local_currency == "USD":
            local_amount = input_amount / rate
        elif currency == "USD" and local_currency == "TWD":
            local_amount = input_amount * rate
        shares = int(local_amount // item["price"]) if item["price"] else 0
        rows.append({
            "symbol": item["symbol"],
            "name": item["name"],
            "market": item["market"],
            "industry": item["industry"],
            "weight_pct": number(item["weight"] * 100),
            "amount": number(input_amount),
            "currency": currency,
            "local_amount": number(local_amount),
            "local_currency": local_currency,
            "shares": shares,
            "price": item["price"],
            "score": item["portfolio_score"],
            "risk_score": item["risk_score"],
            "confidence": item["confidence"],
            "forecast_20d_pct": item["forecast_20d_pct"],
            "target_expected_pct": item.get("target_expected_pct"),
            "target_gap_pct": item.get("target_gap_pct"),
            "reasons": item.get("reasons", [])[:3],
        })
    return rows


def build_portfolio_report(risk_pct: float, profile: str, horizon: str, stats: dict[str, Any]) -> list[str]:
    selected = stats["intervals"]["selected"]
    return [
        f"Profile={profile}, horizon={horizon}, stated risk tolerance={number(risk_pct)}%. The allocation caps single positions, keeps a cash buffer, and diversifies by market and industry.",
        f"Expected annual return is {number(stats['annual_return'] * 100)}% with estimated annual volatility {number(stats['annual_volatility'] * 100)}%. The Sharpe proxy is {number(stats['sharpe_proxy'])}.",
        f"For the selected horizon ({selected['days']} trading days), the historical-normal 80% confidence interval is {selected['ci80_low_pct']}% to {selected['ci80_high_pct']}%; the 95% interval is {selected['ci95_low_pct']}% to {selected['ci95_high_pct']}%.",
        "Confidence intervals are based on recent daily returns and assume the distribution does not abruptly change. News shocks, earnings gaps, liquidity events and FX moves can exceed the interval.",
    ]


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
            title = clean_feed_text(getattr(entry, "title", "") or "")
            summary = clean_feed_text(getattr(entry, "summary", "") or "")
            if title:
                rows.append({"source": source, "title": title, "link": getattr(entry, "link", "") or "", "published": getattr(entry, "published", "") or getattr(entry, "updated", "") or "", "sentiment": score_sentiment(f"{title} {summary}")})
        return rows
    except Exception as exc:
        return [{"source": source, "title": f"News fetch failed: {exc}", "link": "", "published": "", "sentiment": 0}]


def clean_feed_text(value: str) -> str:
    text = html_lib.unescape(re.sub(r"<.*?>", "", value or ""))
    replacements = {
        "â": "'",
        "â": "'",
        "â": '"',
        "â": '"',
        "â": "-",
        "â": "-",
        "â¦": "...",
        "Â ": " ",
        "Â": "",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return re.sub(r"\s+", " ", text).strip()


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


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0
    m = avg(values)
    return math.sqrt(sum((value - m) ** 2 for value in values) / (len(values) - 1))


def bounded(value: float, low: int = 0, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def number(value: Any, digits: int = 2) -> float:
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return 0
        return round(value, digits)
    except Exception:
        return 0
