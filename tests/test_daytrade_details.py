import asyncio
import threading
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException

import openkiri_live as live
from openkiri_cost import calculate_cost, rounded_price
from openkiri_data import iso_time
from openkiri_session import daytrade_status, intraday_status
from test_session_status import epoch, session


NOW = epoch("2026-10-08T10:00:00+08:00")
US_NOW = epoch("2026-10-08T10:00:00-04:00")


def sources(us=False, now=None):
    now = now or (US_NOW if us else NOW)
    chart = {**session(zone="America/New_York" if us else "Asia/Taipei"),
             "as_of": now - 60, "fetched_at": now - 10, "interval": "1m", "time_basis": "bar_start"}
    quote = {"as_of": now - 20, "fetched_at": now - 10, "market_state": "REGULAR",
             "quote_session": "regular", "exchange_timezone": chart["exchange_timezone"]}
    return chart, quote


def fixture(symbol="2330.TW", instrument="EQUITY"):
    us = not symbol.endswith((".TW", ".TWO"))
    now = US_NOW if us else NOW
    chart, price = sources(us)
    meta = {"currentTradingPeriod": chart["sessions"], "exchangeTimezoneName": chart["exchange_timezone"],
            "fetched_at": now - 10, "instrumentType": instrument}
    rows = [{"date": datetime.fromtimestamp(now - (30 - index) * 60, timezone.utc),
             "open": 90 + index / 10, "close": 90.05 + index / 10,
             "high": 90.1 + index / 10, "low": 89.9 + index / 10, "volume": 100 + index,
             "_meta": meta} for index in range(30)]
    quote = {"symbol": symbol, "price": rows[-1]["close"], "currency": "USD" if us else "TWD",
             "instrument_type": instrument, "as_of": iso_time(price["as_of"]), "provenance": {"price": price}}
    benchmark = {"symbol": "^GSPC" if us else "^TWOII" if symbol.endswith(".TWO") else "^TWII",
                 "change_pct": 0.5, "provenance": {"price": deepcopy(price)}}
    return now, rows, quote, benchmark


def us_cost(**changes):
    return {"buy_price": 10, "sell_price": 10.02, "shares": 1,
            "fee_rate": 0, "min_fee": 0, "tax_rate": 0, "fee_discount": 1,
            "tick_size": 0.01, "rounding_mode": "cent", "cost_confirmed": True, **changes}


class DetailSessionTests(unittest.TestCase):
    def test_quote_without_bar_interval_requires_explicit_trade_context(self):
        chart, quote = sources()
        self.assertNotEqual(intraday_status({**chart, **quote, "interval": None}, NOW)["eligibility"], "active")
        result = daytrade_status(chart, quote, NOW)
        self.assertEqual(result["eligibility"], "active")
        self.assertEqual(result["valid_until"], iso_time(NOW + 100))
        self.assertFalse(result["price"]["realtime_verified"])

    def test_stock_bar_and_quote_are_independently_gated(self):
        for target, changes in [("chart", {"as_of": NOW - 180}), ("price", {"as_of": NOW - 120}),
                                ("chart", {"as_of": None}), ("price", {"as_of": NOW + 1}),
                                ("chart", {"interval": "1d"}), ("price", {"delay_seconds": 900}),
                                ("chart", {"delay_seconds": 60}),
                                ("price", {"exchange_timezone": "America/New_York"})]:
            chart, quote = sources()
            (chart if target == "chart" else quote).update(changes)
            with self.subTest(target=target, changes=changes):
                self.assertEqual(daytrade_status(chart, quote, NOW)["eligibility"], "excluded")

    def test_five_minute_bars_have_separate_observation_tolerance(self):
        chart, quote = sources()
        chart.update(interval="5m", as_of=NOW - 600)
        self.assertEqual(daytrade_status(chart, quote, NOW)["eligibility"], "active")
        chart["as_of"] = NOW - 660
        self.assertEqual(daytrade_status(chart, quote, NOW)["reason"], "chart_delayed")

    def test_missing_window_closed_extended_and_boundary_never_execute(self):
        chart, quote = sources()
        self.assertNotEqual(daytrade_status({**chart, "sessions": {}}, quote, NOW)["eligibility"], "active")
        self.assertNotEqual(daytrade_status(chart, quote, epoch("2026-10-08T14:00:00+08:00"))["eligibility"], "active")
        chart, quote = sources(True)
        self.assertNotEqual(daytrade_status(chart, quote, epoch("2026-10-08T17:00:00-04:00"))["eligibility"], "active")
        close = epoch("2026-10-08T16:00:00-04:00")
        chart.update(as_of=close - 60, fetched_at=close - 10)
        quote.update(as_of=close - 20, fetched_at=close - 10)
        self.assertEqual(daytrade_status(chart, quote, close - 1)["valid_until"], iso_time(close))
        self.assertNotEqual(daytrade_status(chart, quote, close)["eligibility"], "active")


class DaytradeModelTests(unittest.TestCase):
    def model(self, symbol="2330.TW", payload=None, change=None, rows_override=None):
        now, rows, quote, benchmark = fixture(symbol)
        if change:
            change(rows, quote, benchmark)
        with patch("time.time", return_value=now), patch.object(live.base, "fetch_price_history", return_value=rows if rows_override is None else rows_override), \
                patch.object(live.base, "latest_quote_payload", return_value=quote), \
                patch.object(live.base, "safe_quote_or_none", return_value=benchmark) as get_index:
            result = live.base.daytrade_analyze_payload(symbol, payload or {})
        return result, get_index

    def assertBlocked(self, result):
        self.assertFalse(result["execution"]["allowed"])
        self.assertIsNone(result["score"])
        self.assertIsNone(result["playbook"])
        self.assertIsNone(result["cost"])
        self.assertEqual(result["levels"], {})
        self.assertEqual(result["risk_gate"]["state"], "blocked")

    def test_taiwan_stock_uses_taiwan_benchmark_and_conditional_cost(self):
        result, index = self.model()
        index.assert_called_once_with("^TWII")
        self.assertTrue(result["execution"]["allowed"])
        self.assertEqual(result["cost"]["currency"], "TWD")
        self.assertEqual(result["cost"]["assumptions"]["sell_rate"], 0.0015)
        self.assertEqual(result["provenance"]["chart"]["as_of"], iso_time(NOW - 60))

    def test_us_zero_fee_scenario_never_inherits_taiwan_index_tax_or_grid(self):
        result, index = self.model("MU", us_cost(buy_price=92.95, sell_price=93))
        index.assert_called_once_with("^GSPC")
        self.assertTrue(result["execution"]["allowed"])
        cost = result["cost"]
        self.assertEqual(cost["currency"], "USD")
        self.assertEqual(cost["total_cost"], 0)
        self.assertEqual(cost["break_even_sell_price"], 92.95)
        self.assertEqual(cost["tick_size"], 0.01)

    def test_otc_uses_otc_index(self):
        result, index = self.model("6488.TWO")
        index.assert_called_once_with("^TWOII")
        self.assertTrue(result["execution"]["allowed"])

    def test_empty_or_delayed_bars_never_synthesize_current_data(self):
        result, index = self.model(rows_override=[])
        self.assertBlocked(result)
        self.assertIsNone(result["provenance"]["chart"]["as_of"])
        index.assert_not_called()
        for kind in ("chart", "quote"):
            def change(rows, quote, benchmark):
                if kind == "chart":
                    rows[-1]["date"] = datetime.fromtimestamp(NOW - 1200, timezone.utc)
                else:
                    quote["provenance"]["price"]["as_of"] = NOW - 1200
            result, index = self.model(change=change)
            self.assertBlocked(result)
            index.assert_not_called()

    def test_bad_or_wrong_market_benchmarks_do_not_become_neutral(self):
        for changes in ({"symbol": "^TWII"}, {"change_pct": None}, {"change_pct": float("nan")}, {"provenance": {}}):
            with self.subTest(changes=changes):
                result, _ = self.model("MU", us_cost(), lambda rows, quote, index: index.update(changes))
                self.assertBlocked(result)
                self.assertEqual(result["execution"]["reason"], "benchmark_unavailable")
        result, _ = self.model(change=lambda rows, quote, index: index["provenance"]["price"].update(as_of=NOW - 120))
        self.assertBlocked(result)

    def test_expiry_after_slow_benchmark_lookup_is_rechecked(self):
        now, rows, quote, index = fixture()
        times = iter([now, now + 130])
        with patch("time.time", side_effect=lambda: next(times, now + 130)), \
                patch.object(live.base, "fetch_price_history", return_value=rows), \
                patch.object(live.base, "latest_quote_payload", return_value=quote), \
                patch.object(live.base, "safe_quote_or_none", return_value=index):
            self.assertBlocked(live.base.daytrade_analyze_payload("2330.TW", {}))

    def test_cost_confirmation_currency_identity_and_malformed_input_fail_closed(self):
        result, index = self.model("MU")
        self.assertBlocked(result)
        self.assertEqual(result["execution"]["reason"], "cost_unconfirmed")
        index.assert_not_called()
        for payload in ({"buy_price": []}, {"shares": 0}, {"fee_rate": -1}):
            result, index = self.model(payload=payload)
            self.assertBlocked(result)
            self.assertEqual(result["execution"]["reason"], "cost_inputs")
            index.assert_not_called()
        result, _ = self.model(change=lambda rows, quote, index: quote.update(currency="USD"))
        self.assertBlocked(result)
        with self.assertRaises(HTTPException) as error:
            self.model(change=lambda rows, quote, index: quote.update(symbol="MU"))
        self.assertEqual(error.exception.status_code, 502)

    def test_only_regular_session_valid_bars_enter_the_model(self):
        result, index = self.model(change=lambda rows, quote, index: rows[0].update(close=float("nan")))
        self.assertBlocked(result)
        index.assert_not_called()
        result, _ = self.model(change=lambda rows, quote, index: rows[0].update(date=datetime.fromtimestamp(NOW - 86400, timezone.utc), high=9999))
        self.assertTrue(result["execution"]["allowed"])
        self.assertLess(result["levels"]["breakout_trigger"], 100)

    def test_instrument_type_conflict_is_blocked(self):
        result, index = self.model(change=lambda rows, quote, index: quote.update(instrument_type="ETF"))
        self.assertBlocked(result)
        index.assert_not_called()


class CostScenarioTests(unittest.TestCase):
    def test_zero_rates_and_discount_are_not_defaulted(self):
        cost = calculate_cost(us_cost(), "US")
        self.assertEqual(cost["total_cost"], 0)
        self.assertEqual(cost["net_profit"], 0.02)
        self.assertEqual(cost["break_even_sell_price"], 10)
        cost = calculate_cost(us_cost(fee_rate=0.1, fee_discount=0), "US")
        self.assertEqual(cost["total_cost"], 0)

    def test_us_fees_use_cents_not_taiwan_integer_rounding(self):
        cost = calculate_cost(us_cost(fee_rate=0.0003, tax_rate=0.0002, min_fee=0.01), "US")
        self.assertEqual((cost["buy_fee"], cost["sell_fee"], cost["tax"]), (0.01, 0.01, 0.01))
        self.assertEqual(cost["net_profit"], -0.01)
        self.assertEqual(cost["break_even_sell_price"], 10.03)

    def test_taiwan_daytrade_and_normal_tax_differ(self):
        data = {"buy_price": 68.4, "sell_price": 69.4, "shares": 1000}
        cost = calculate_cost(data, "TW")
        self.assertEqual((cost["buy_fee"], cost["sell_fee"], cost["tax"]), (97, 98, 104))
        self.assertEqual(cost["net_profit"], 701)
        self.assertEqual(calculate_cost({**data, "is_daytrade": False}, "TW")["tax"], 208)

    def test_etf_requires_explicit_sell_rate_and_uses_etf_price_grid(self):
        with self.assertRaises(HTTPException):
            calculate_cost({"buy_price": 106.2, "instrument_type": "ETF"}, "TW")
        cost = calculate_cost({"buy_price": 106.2, "instrument_type": "ETF", "cost_confirmed": True, "tax_rate": 0.001}, "TW")
        self.assertEqual(cost["tick_size"], 0.05)
        self.assertEqual(cost["profile"], "broker_scenario")
        self.assertEqual(float(rounded_price(49.999, instrument="ETF")), 50)

    def test_invalid_missing_nonfinite_negative_and_fractional_inputs_are_rejected(self):
        for key, bad in [("fee_rate", None), ("fee_rate", float("nan")), ("min_fee", -1), ("min_fee", 0.001),
                         ("sell_extra_fee", 0.001), ("shares", 0), ("shares", 1.5), ("tick_size", 0),
                         ("buy_price", float("inf")), ("fee_discount", 2), ("rounding_mode", "bad"), ("cost_confirmed", False)]:
            with self.subTest(key=key, bad=bad), self.assertRaises(HTTPException) as error:
                calculate_cost(us_cost(**{key: bad}), "US")
            self.assertEqual(error.exception.status_code, 422)

    def test_break_even_search_has_no_four_thousand_tick_limit(self):
        data = us_cost(buy_price=1000, sell_price=1000, min_fee=30)
        cost = calculate_cost(data, "US")
        self.assertEqual(cost["break_even_sell_price"], 1060)
        self.assertEqual(cost["min_profit_ticks"], 6000)
        self.assertEqual(calculate_cost({**data, "sell_price": 1060}, "US")["net_profit"], 0)
        self.assertLess(calculate_cost({**data, "sell_price": 1059.99}, "US")["net_profit"], 0)

    def test_taiwan_graduated_tick_boundary_and_us_subcent_scenario(self):
        self.assertEqual(float(rounded_price(49.999)), 50)
        self.assertEqual(float(rounded_price(50.001)), 50.1)
        cost = calculate_cost(us_cost(buy_price=10.0001, sell_price=10.0002, tick_size=0.0001), "US")
        self.assertEqual(cost["break_even_sell_price"], 10.0001)

    def test_rounded_zero_profit_does_not_pass_cost_gate(self):
        cost = calculate_cost(us_cost(buy_price=10.0002, sell_price=10.0001, tick_size=0.0001), "US")
        self.assertEqual(cost["net_profit"], 0)
        self.assertLess(cost["net_return_pct"], 0)
        _, rows, _, index = fixture("MU")
        metrics = live.base.intraday_metrics(rows, index, cost)
        self.assertFalse(metrics["cost_covered"])
        self.assertIn("毛利不足以覆蓋稅費", live.base.risk_gate(metrics, cost, 75)["red"])


class DaytradeRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_provider_model_runs_off_event_loop(self):
        entered, release = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        worker_ids = []
        payload = {"symbol": "mu", "fee_rate": 0}

        class Request:
            async def json(self):
                return payload

        def model(symbol, inputs):
            worker_ids.append(threading.get_ident())
            self.assertEqual(symbol, "MU")
            self.assertIs(inputs, payload)
            entered.set()
            release.wait(0.5)
            return {"ok": True, "symbol": symbol}

        with patch.object(live.base, "daytrade_analyze_payload", side_effect=model):
            task = asyncio.create_task(live.base.daytrade_analyze(Request()))
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(entered.is_set())
                self.assertNotEqual(worker_ids, [main_thread])
                self.assertFalse(task.done(), "event loop remains available while model work is pending")
            finally:
                release.set()
            self.assertEqual((await asyncio.wait_for(task, 2))["symbol"], "MU")

    async def test_worker_errors_preserve_http_exception_contract(self):
        class Request:
            async def json(self):
                return {"symbol": "MU"}

        with patch.object(live.base, "daytrade_analyze_payload", side_effect=HTTPException(502, "Provider unavailable")):
            with self.assertRaises(HTTPException) as error:
                await live.base.daytrade_analyze(Request())
        self.assertEqual(error.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
