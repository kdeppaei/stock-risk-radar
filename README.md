# OpenKiri

OpenKiri is a FastAPI stock-risk dashboard for Taiwan and US stocks. It groups tickers by trend-continuation setups, shows technical risk, and displays floating market-cap and P/E valuation when Yahoo data is available.

Public website: https://stock-risk-radar.onrender.com/#


## Features

- Clean single-screen stock dashboard for Taiwan and US symbols such as `2330`, `2330.TW`, `AAPL`, `NVDA`, and `TSLA`.
- Yahoo chart data with MA5, MA20, MA60, RSI, MACD, ATR, volume ratio, and 20-day return.
- Setup filters for golden-cross continuation, golden-cross watch, death-cross continuation, death-cross watch, bullish/bearish MA continuation, volume breakout, MA20 pullback hold, oversold rebound, overheat risk, and mixed consolidation.
- Screener grouping by market, industry, setup type, signal score, low P/E, market cap, risk, change, price, or volume.
- Floating valuation cards for market cap, trailing P/E, and forward P/E from Yahoo quoteSummary, with a static market-cap fallback.
- Responsive UI focused on classification and comparison instead of crowded dashboards.

## Local Run

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn openkiri_app:app --reload --host 127.0.0.1 --port 8000
```

Open locally:

```text
http://127.0.0.1:8000
```

## Deploy To Render

Build Command:

```text
pip install -r requirements.txt
```

Start Command:

```text
uvicorn openkiri_app:app --host 0.0.0.0 --port $PORT
```

The included `render.yaml` keeps the existing `stock-risk-radar` Render service name while using the OpenKiri app entry point.

## API

```text
GET /health
GET /api/screener/options
GET /api/analyze?symbol=AAPL&period=6mo&interval=1d
GET /api/screener?markets=US,TW&industries=Semiconductors,Technology&setup=golden_cross_continuation&sort_by=signal&limit=30
GET /api/recommendations?markets=US,TW&limit=8
```

Supported `period`: `1mo`, `3mo`, `6mo`, `1y`, `2y`, `5y`

Supported `interval`: `1d`, `1wk`

## Disclaimer

This tool is for research and education only. It is not investment advice.
