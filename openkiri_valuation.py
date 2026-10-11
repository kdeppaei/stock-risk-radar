"""Public, dated valuation inputs; estimates never masquerade as reported values."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from openkiri_data import empty_valuation, finite, iso_time

TWSE = "https://openapi.twse.com.tw/v1"
TPEX = "https://www.tpex.org.tw/openapi/v1"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def official_date(value: Any) -> str | None:
    text = str(value or "").replace("/", "").replace("-", "")
    try:
        year = int(text[:-4]) + (1911 if len(text) == 7 else 0)
        if len(text) not in {7, 8}:
            return None
        return iso_time(datetime(year, int(text[-4:-2]), int(text[-2:]),
                                 tzinfo=timezone(timedelta(hours=8))))
    except (ValueError, TypeError):
        return None


def recent(value: Any, max_days: int, now: float | None = None) -> bool:
    stamp = iso_time(value)
    if not stamp:
        return False
    age = (time.time() if now is None else now) - datetime.fromisoformat(stamp).timestamp()
    return -86400 <= age <= max_days * 86400


def number(value: Any) -> float | None:
    return finite(str(value).replace(",", ""), positive=True)


def public_json(url: str, params: dict | None = None) -> tuple[Any, str | None]:
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=(2, 3))
        response.raise_for_status()
        if len(response.content) > 3_000_000:
            return {}, None
        data = json.loads(response.content)
        return data if isinstance(data, (dict, list)) else {}, iso_time(getattr(response, "openkiri_fetched_at", time.time()))
    except (requests.RequestException, ValueError):
        return {}, None


def valuation_price(symbol: str) -> dict[str, Any]:
    data, fetched = public_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                                {"range": "5d", "interval": "1d"})
    try:
        meta = data["chart"]["result"][0]["meta"]
        if str(meta.get("symbol", "")).upper() != symbol.upper():
            return {}
        price = finite(meta.get("regularMarketPrice"), positive=True)
        as_of = iso_time(meta.get("regularMarketTime"))
        if not price or not recent(as_of, 14):
            return {}
        return {"symbol": symbol, "price": price, "currency": meta.get("currency"),
                "as_of": as_of, "fetched_at": fetched, "source": "Yahoo chart",
                "name": str(meta.get("longName") or meta.get("shortName") or "")}
    except (KeyError, IndexError, TypeError):
        return {}


def taiwan_valuation(symbol: str, profile: dict, ratios: dict, price: dict,
                     fetched: str | None, now: float | None = None) -> dict:
    result = empty_valuation(symbol, "TWD")
    code, suffix = symbol.rsplit(".", 1)
    otc = suffix == "TWO"
    source = "TPEx" if otc else "TWSE"
    fields = {}
    date = official_date(ratios.get("Date"))
    if str(ratios.get("SecuritiesCompanyCode" if otc else "Code")) == code and recent(date, 14, now):
        pe = number(ratios.get("PriceEarningRatio" if otc else "PEratio"))
        if pe:
            result["trailing_pe"] = pe
            fields["trailing_pe"] = {"source": source, "as_of": date, "estimated": False,
                                     "basis": "exchange_daily_pe"}
    # TDR profiles describe underlying foreign shares, not local depositary units.
    profile_code = profile.get("SecuritiesCompanyCode" if otc else "公司代號")
    shares_date = official_date(profile.get("Date" if otc else "出表日期"))
    shares = number(profile.get("IssueShares" if otc else "已發行普通股數或TDR原股發行股數"))
    if (str(profile_code) == code and not code.startswith("91") and shares
            and recent(shares_date, 30, now) and price.get("symbol") == symbol
            and price.get("currency") == "TWD" and recent(price.get("as_of"), 14, now)):
        cap = finite(shares * (finite(price.get("price"), positive=True) or 0), positive=True)
        if cap:
            result.update(market_cap=cap, shares_outstanding=shares, estimated=True, fallback=True)
            fields["market_cap"] = {"source": source + " + Yahoo chart", "as_of": price["as_of"],
                "estimated": True, "basis": "price_times_issued_shares", "shares_as_of": shares_date,
                "shares_date_basis": "profile_snapshot", "price_as_of": price["as_of"]}
    if otc and str(profile_code) == code and recent(shares_date, 14, now):
        cap = number(profile.get("MarketValue"))
        issued = number(profile.get("Capitals"))
        close = number(profile.get("ClosePrice"))
        # TPEx reports rounded millions; cross-check the unit against shares * close.
        if cap and issued and close and abs(cap * 1_000_000 - issued * close) <= 1_000_000:
            result.update(market_cap=cap * 1_000_000, shares_outstanding=issued, estimated=False, fallback=False)
            fields["market_cap"] = {"source": source, "as_of": shares_date, "estimated": False,
                                    "basis": "exchange_daily_market_cap"}
    result.update(source=source, fetched_at=fetched, fields=fields)
    if fields:
        result["status"] = "derived" if result["estimated"] else "reported"
        result["as_of"] = None if result["estimated"] else fields.get("market_cap", {}).get("as_of", date)
    return result


def latest_financial(symbol: str, data: dict, key: str, now: float | None = None) -> dict:
    candidates = []
    try:
        for row in data.get("timeseries", {}).get("result") or []:
            if row.get("meta", {}).get("symbol") != [symbol]:
                continue
            for entry in row.get(key) or []:
                date = iso_time(entry.get("asOfDate"))
                value = finite(entry.get("reportedValue"))
                if value is not None and recent(date, 400, now) and entry.get("periodType") == "TTM":
                    candidates.append({"value": value, "as_of": date, "currency": entry.get("currencyCode")})
    except (AttributeError, TypeError):
        return {}
    latest = max(candidates, key=lambda row: row["as_of"], default={})
    return latest if latest.get("value", 0) > 0 else {}


def us_valuation(symbol: str, summary: dict, financials: dict, price: dict,
                 fetched: str | None, now: float | None = None) -> dict:
    result = empty_valuation(symbol, "USD")
    fields = {}
    data = summary.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    if data.get("symbol") == symbol and str(data.get("assetClass", "")).upper() == "STOCKS":
        cap = number((data.get("summaryData", {}).get("MarketCap") or {}).get("value"))
        if cap:
            result["market_cap"] = cap
            fields["market_cap"] = {"source": "Nasdaq", "as_of": None, "estimated": False,
                                     "basis": "provider_reported"}
    eps = latest_financial(symbol, financials, "trailingDilutedEPS", now)
    name = str(price.get("name", "")).lower()
    # EPS for underlying shares is not interchangeable with ADR/depositary units.
    depositary = any(term in name for term in ("adr", "depositary", "depository"))
    if (eps and not depositary and price.get("symbol") == symbol and price.get("currency") == "USD"
            and eps["currency"] == "USD" and recent(price.get("as_of"), 14, now)
            and eps["as_of"] <= (iso_time(price.get("as_of")) or "")):
        pe = finite((finite(price.get("price"), positive=True) or 0) / eps["value"], positive=True)
        if pe:
            result.update(trailing_pe=pe, estimated=True)
            fields["trailing_pe"] = {"source": "Yahoo fundamentals-timeseries + Yahoo chart",
                "as_of": eps["as_of"], "estimated": True, "basis": "price_over_ttm_diluted_eps",
                "eps_ttm": eps["value"], "price_as_of": price["as_of"]}
    result.update(source="Nasdaq" if result["market_cap"] else None, fetched_at=fetched, fields=fields)
    if fields:
        result["status"] = "derived" if result["estimated"] else "reported"
        result["source"] = result["source"] or "Yahoo fundamentals-timeseries"
    return result


def public_valuation(symbol: str) -> dict:
    """Three bounded reads in parallel. Official daily datasets are shared for six hours."""
    with ThreadPoolExecutor(max_workers=3) as pool:
        price_future = pool.submit(valuation_price, symbol)
        if symbol.endswith((".TW", ".TWO")):
            otc = symbol.endswith(".TWO")
            root = TPEX if otc else TWSE
            profile_future = pool.submit(public_json, root + ("/tpex_daily_market_value" if otc else "/opendata/t187ap03_L"))
            ratios_future = pool.submit(public_json, root + ("/tpex_mainboard_peratio_analysis" if otc else "/exchangeReport/BWIBBU_ALL"))
            profiles, profile_fetched = profile_future.result()
            ratios, ratios_fetched = ratios_future.result()
            code = symbol.split(".")[0]
            profile_key = "SecuritiesCompanyCode" if otc else "公司代號"
            ratio_key = "SecuritiesCompanyCode" if otc else "Code"
            profile = next((row for row in profiles if isinstance(row, dict) and row.get(profile_key) == code), {})
            ratio = next((row for row in ratios if isinstance(row, dict) and row.get(ratio_key) == code), {})
            result = taiwan_valuation(symbol, profile, ratio, price_future.result(),
                                      max(filter(None, [profile_fetched, ratios_fetched]), default=None))
            for key, field in result["fields"].items():
                field["fetched_at"] = ratios_fetched if key == "trailing_pe" else profile_fetched if otc else price_future.result().get("fetched_at")
                if key == "market_cap" and field.get("estimated"):
                    field["shares_fetched_at"] = profile_fetched
        else:
            summary_future = pool.submit(public_json, f"https://api.nasdaq.com/api/quote/{symbol}/summary", {"assetclass": "stocks"})
            # A day-rounded range lets all lookups of one ticker share the HTTP cache.
            end = int(time.time() // 86400 * 86400)
            financial_future = pool.submit(public_json,
                f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}",
                {"type": "trailingDilutedEPS", "period1": end - 730 * 86400, "period2": end})
            summary, fetched = summary_future.result()
            financials, financial_fetched = financial_future.result()
            result = us_valuation(symbol, summary if isinstance(summary, dict) else {},
                                  financials if isinstance(financials, dict) else {}, price_future.result(),
                                  max(filter(None, [fetched, financial_fetched]), default=None))
            for key, field in result["fields"].items():
                field["fetched_at"] = fetched if key == "market_cap" else financial_fetched
    return result
