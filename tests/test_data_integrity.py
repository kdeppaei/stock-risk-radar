import asyncio
import json
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import requests
import openkiri_live as live
from openkiri_data import finite, iso_time, valuation_from_snapshot, valuation_from_summary
from openkiri_metrics import Metrics, MetricsMiddleware, provider_group


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result.url = "https://query1.finance.yahoo.com/test"
    result.openkiri_fetched_at = 1770000000
    return result


class ProvenanceTests(unittest.TestCase):
    def test_valuation_never_infers_money_flow_or_timestamp(self):
        v = valuation_from_snapshot("MU", {"symbol": "MU", "currency": "USD", "market_cap": 1000,
            "change_pct": 5, "as_of": 1760000000, "fetched_at": "2026-01-01T00:00:00Z", "source": "Yahoo quote"})
        self.assertEqual(v["market_cap"], 1000)
        self.assertEqual(v["status"], "reported")
        self.assertIsNone(v["market_cap_change_pct"])
        self.assertIsNone(v["market_cap_previous"])
        self.assertIsNone(v["as_of"])
        self.assertNotEqual(v["quote_as_of"], v["fetched_at"])

    def test_wrong_symbol_or_missing_currency_is_not_accepted(self):
        self.assertIsNone(valuation_from_snapshot("MU", {"symbol": "2330.TW", "currency": "TWD", "market_cap": 999})["market_cap"])
        self.assertIsNone(valuation_from_snapshot("MU", {"symbol": "MU", "market_cap": 999})["market_cap"])
        self.assertEqual(valuation_from_summary("MU", {"price": {"symbol": "AAPL", "currency": "USD", "marketCap": 999}}, time.time())["status"], "unavailable")

    def test_raw_fields_and_invalid_ratios(self):
        v = valuation_from_summary("MU", {"price": {"symbol": "MU", "currency": "USD", "marketCap": {"raw": 123}},
            "summaryDetail": {"trailingPE": {"raw": -10}, "forwardPE": {"raw": 12}}}, 1770000000)
        self.assertEqual(v["market_cap"], 123)
        self.assertEqual(v["forward_pe"], 12)
        self.assertIsNone(v["trailing_pe"])
        self.assertIsNone(finite(float("nan")))
        self.assertIsNone(iso_time("not a date"))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        live.HTTP_GET_CACHE.clear()
        live.base.QUOTE_CACHE.clear()
        live.VALUATION_CACHE.clear()
        live.ANALYZE_CACHE.clear()

    def test_history_rejects_wrong_provider_identity(self):
        with patch.object(live.requests, "get", return_value=response({"chart": {"result": [{"meta": {"symbol": "2330.TW"}}]}})):
            self.assertEqual(live._ORIGINAL_FETCH_PRICE_HISTORY("MU", "1d", "5m"), [])

    def test_quote_selects_matching_row_and_keeps_provider_time(self):
        payload = {"quoteResponse": {"result": [{"symbol": "2330.TW", "regularMarketPrice": 2000},
            {"symbol": "MU", "regularMarketPrice": 100, "regularMarketTime": 1760000000, "currency": "USD"}]}}
        with patch.object(live.requests, "get", return_value=response(payload)):
            quote = live.fetch_yahoo_quote_snapshot("MU")
        self.assertEqual(quote["price"], 100)
        self.assertEqual(quote["as_of"], iso_time(1760000000))
        self.assertEqual(quote["fetched_at"], iso_time(1770000000))

    def test_chart_quote_fallback_preserves_time_and_unavailable_valuation(self):
        dt = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
        row = {"date": dt, "date_label": "2026-01-02 14:30", "close": 100, "open": 99, "high": 101, "low": 98, "volume": 50,
               "_meta": {"currency": "USD", "chartPreviousClose": 98, "fetched_at": 1770000000}}
        with patch.object(live, "fetch_yahoo_quote_snapshot", return_value={}), patch.object(live.base, "fetch_price_history", return_value=[row]):
            quote = live.latest_quote_payload("MU")
            again = live.latest_quote_payload("MU")
        self.assertEqual(quote["as_of"], dt.isoformat())
        self.assertEqual(again["as_of"], quote["as_of"])
        self.assertIsNone(quote["market_cap"])
        self.assertIsNone(quote["market_cap_change_pct"])
        self.assertIsNone(quote["volume_ratio"])

    def test_valuation_outage_does_not_use_universe_estimate(self):
        with patch.object(live, "fetch_yahoo_quote_snapshot", return_value={}), patch.object(live.base, "yahoo_quote_summary", return_value={}) as fetch:
            value = live.verified_stock_valuation({"symbol": "MU", "market_cap_usd": 170000000000}, 100)
            live.verified_valuation("MU")
        self.assertIsNone(value["market_cap"])
        self.assertEqual(value["market_cap_usd"], 0)
        self.assertEqual(fetch.call_count, 1)

    def test_simultaneous_cache_misses_share_one_request(self):
        def get(*args, **kwargs):
            time.sleep(.03)
            return response({"ok": True})
        with patch.object(live, "_ORIGINAL_REQUESTS_GET", side_effect=get) as fetch:
            with ThreadPoolExecutor(max_workers=5) as pool:
                rows = list(pool.map(lambda _: live.bandwidth_cached_get("https://query1.finance.yahoo.com/v7/finance/quote", params={"symbols": "MU"}), range(5)))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len({row.openkiri_fetched_at for row in rows}), 1)

    def test_negative_cache_avoids_repeating_denied_requests(self):
        with patch.object(live, "_ORIGINAL_REQUESTS_GET", return_value=response({}, 401)) as fetch:
            for _ in range(3):
                self.assertEqual(live.bandwidth_cached_get("https://query1.finance.yahoo.com/v7/finance/quote").status_code, 401)
        self.assertEqual(fetch.call_count, 1)

    def test_analysis_does_not_block_on_news_or_valuation(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [{"date": start + timedelta(days=i), "date_label": (start + timedelta(days=i)).strftime("%Y-%m-%d"),
                 "open": 100+i, "high": 102+i, "low": 99+i, "close": 101+i, "volume": 1000,
                 "_meta": {"currency": "USD", "fetched_at": 1770000000}} for i in range(80)]
        with patch.object(live.base, "fetch_price_history", return_value=rows), patch.object(live.base, "fetch_news", side_effect=AssertionError("unexpected news fetch")), patch.object(live.base, "save_log"), patch.object(live.base, "stock_valuation_payload", side_effect=AssertionError("unexpected valuation fetch")):
            result = live.analyze("MU", "1y", "1d", False)
        self.assertEqual(result["analysis_scope"], "technical_only")
        self.assertEqual(result["symbol"], "MU")
        self.assertEqual(result["provenance"]["chart"]["as_of"], iso_time(rows[-1]["date"]))
        self.assertIsNone(result["valuation"]["market_cap"])


class MetricsTests(unittest.TestCase):
    def test_metrics_bound_storage_and_sanitize_provider_names(self):
        metrics = Metrics()
        for _ in range(200):
            metrics.record("Cache Yahoo quote", hit=True)
        metrics.record("Upstream Yahoo quote", elapsed=.2, sent=40, received=400, status=429)
        data = metrics.snapshot()
        self.assertEqual(data["rows"][0]["cache_hit_pct"], 100)
        self.assertEqual(len(metrics.groups["Cache Yahoo quote"]["latencies"]), 128)
        self.assertEqual(provider_group("https://query1.finance.yahoo.com/v7/finance/quote?symbols=SECRET&crumb=SECRET"), "Yahoo quote")
        self.assertNotIn("SECRET", json.dumps(data))

    def test_middleware_records_body_and_route_template_only(self):
        async def app(scope, receive, send):
            scope["route"] = SimpleNamespace(path="/api/quote/{symbol}")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"12345"})
        async def send(message):
            pass
        metrics = Metrics()
        with patch("openkiri_metrics.METRICS", metrics):
            asyncio.run(MetricsMiddleware(app)({"type": "http", "path": "/api/quote/SECRET"}, None, send))
        row = metrics.snapshot()["rows"][0]
        self.assertEqual(row["name"], "API /api/quote/{symbol}")
        self.assertEqual(row["sent_bytes"], 5)


if __name__ == "__main__":
    unittest.main()
