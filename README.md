# Stock Risk Radar

FastAPI stock-risk dashboard for Taiwan and US stocks.

Users can enter symbols such as `2330`, `2330.TW`, `6488.TWO`, `AAPL`, `NVDA`, and `TSLA`. The app fetches price data, calculates technical indicators, reads RSS finance news, and returns a dashboard with candlesticks, support/resistance, risk score, news sentiment, and intraday/short-term/long-term suitability.
https://stock-risk-radar.onrender.com/#

## Features

- FastAPI backend
- Yahoo chart API price fetch
- Google News RSS / Yahoo Finance RSS
- Candlestick chart rendered in browser canvas
- Taiwan market colors: up red, down green
- US market colors: up green, down red
- MA5, MA20, MA60, RSI, MACD, ATR, volume ratio
- 1-day, 5-day, and 20-day returns
- 60-day support/resistance, breakout call point, backtest zone, stop-loss reference
- Live quote refresh, watchlist alerts, and market context
- Stock screener for US/Taiwan markets with price range, industry filters, and quality/market-cap/volume sorting
- Recent recommendation board with regression, technical, news-sentiment, and liquidity/market-cap scoring
- Finance headline ticker and YouTube finance live-search shortcuts

## Local Run

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Open locally:

```text
http://127.0.0.1:8000
```

Public website:

```text
https://stock-risk-radar.onrender.com
```

## Deploy To Render

Create a GitHub repository and push this folder.

On Render, create a Web Service from the GitHub repository.

Build Command:

```text
pip install -r requirements.txt
```

Start Command:

```text
uvicorn app:app --host 0.0.0.0 --port $PORT
```

The included `render.yaml` can also be used as a Render Blueprint.

After deployment, open:

```text
https://stock-risk-radar.onrender.com
```

## Why Not GitHub Pages Only?

GitHub Pages is for static HTML/CSS/JavaScript. This project needs a Python server to fetch market data, read RSS news, calculate indicators, and serve API responses. Use Render, Railway, Fly.io, or another Python hosting platform for the FastAPI backend.

## API

```text
GET /api/analyze?symbol=AAPL&period=1y&interval=1d
GET /api/screener?markets=US,TW&industries=Semiconductors,Technology&min_price=10&max_price=1000&sort_by=quality
GET /api/recommendations?markets=US,TW&limit=8
```

Supported `period`: `1d`, `5d`, `1mo`, `6mo`, `1y`, `2y`, `5y`

Supported `interval`: `5m`, `15m`, `1h`, `1d`, `1wk`

## Disclaimer

This tool is for research and education only. It is not investment advice.
