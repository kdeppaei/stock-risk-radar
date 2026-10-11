import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import openkiri_live as live
import requests
from openkiri_cache_policy import http_ttl
from openkiri_valuation import official_date, public_valuation, taiwan_valuation, us_valuation, valuation_price

NOW = datetime(2026, 10, 11, tzinfo=timezone.utc).timestamp()


def response(payload):
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(payload).encode()
    result.url = "https://openapi.twse.com.tw/test"
    return result


def price(symbol="2330.TW", currency="TWD"):
    return {"symbol": symbol, "currency": currency, "price": 100,
            "as_of": "2026-10-08T05:30:00+00:00", "name": "Common Stock"}


def financials(value=4, symbol="MU", currency="USD"):
    return {"timeseries": {"result": [{"meta": {"symbol": [symbol]}, "trailingDilutedEPS": [
        {"asOfDate": "2026-05-31", "periodType": "TTM", "currencyCode": currency,
         "reportedValue": {"raw": value}}]}]}}


class PublicValuationTests(unittest.TestCase):
    def test_official_dates_and_invalid_dates(self):
        self.assertEqual(official_date("1151008"), "2026-10-07T16:00:00+00:00")
        self.assertEqual(official_date("20261008"), official_date("1151008"))
        self.assertIsNone(official_date("1151399"))

    def test_taiwan_cap_is_estimate_and_pe_is_official(self):
        profile = {"公司代號": "2330", "出表日期": "1151010", "已發行普通股數或TDR原股發行股數": "1000"}
        ratios = {"Code": "2330", "Date": "1151008", "PEratio": "20"}
        v = taiwan_valuation("2330.TW", profile, ratios, price(), None, NOW)
        self.assertEqual(v["market_cap"], 100000)
        self.assertEqual(v["trailing_pe"], 20)
        self.assertTrue(v["fields"]["market_cap"]["estimated"])
        self.assertFalse(v["fields"]["trailing_pe"]["estimated"])
        self.assertEqual(v["fields"]["market_cap"]["shares_as_of"], official_date("1151010"))
        self.assertIsNone(v["as_of"])
        self.assertIsNone(v["market_cap_change_pct"])

    def test_taiwan_wrong_exchange_currency_and_tdr_shares_rejected(self):
        profile = {"公司代號": "2330", "出表日期": "1151010", "已發行普通股數或TDR原股發行股數": "1000"}
        for wrong in [price("2330.TW", "USD"), price("2330.TWO"), price("MU")]:
            self.assertIsNone(taiwan_valuation("2330.TW", profile, {}, wrong, None, NOW)["market_cap"])
        self.assertIsNone(taiwan_valuation("2330.TWO", profile, {}, price("2330.TWO"), None, NOW)["market_cap"])
        profile["公司代號"] = "9105"
        self.assertIsNone(taiwan_valuation("9105.TW", profile, {}, price("9105.TW"), None, NOW)["market_cap"])

    def test_stale_shares_and_stale_pe_are_unavailable(self):
        profile = {"公司代號": "2330", "出表日期": "1140101", "已發行普通股數或TDR原股發行股數": "1000"}
        ratios = {"Code": "2330", "Date": "1140101", "PEratio": "20"}
        self.assertEqual(taiwan_valuation("2330.TW", profile, ratios, price(), None, NOW)["status"], "unavailable")

    def test_otc_fields_and_non_par_value_shares(self):
        v = taiwan_valuation("6488.TWO", {"SecuritiesCompanyCode": "6488", "Date": "1151010", "IssueShares": "2000"},
                             {"SecuritiesCompanyCode": "6488", "Date": "1151008", "PriceEarningRatio": "30"},
                             price("6488.TWO"), None, NOW)
        self.assertEqual(v["market_cap"], 200000)
        self.assertEqual(v["trailing_pe"], 30)
        self.assertEqual(v["source"], "TPEx")

    def test_us_separates_reported_cap_from_derived_pe(self):
        summary = {"data": {"symbol": "MU", "assetClass": "STOCKS", "summaryData": {"MarketCap": {"value": "1,234,000"}}}}
        v = us_valuation("MU", summary, financials(), price("MU", "USD"), None, NOW)
        self.assertEqual(v["market_cap"], 1234000)
        self.assertEqual(v["trailing_pe"], 25)
        self.assertFalse(v["fields"]["market_cap"]["estimated"])
        self.assertTrue(v["fields"]["trailing_pe"]["estimated"])
        self.assertIsNone(v["fields"]["market_cap"]["as_of"])
        self.assertIsNone(v["forward_pe"])

    def test_otc_reported_cap_units_are_verified(self):
        row = {"SecuritiesCompanyCode": "6488", "Date": "1151008", "MarketValue": "200", "Capitals": "2000000", "ClosePrice": "100"}
        v = taiwan_valuation("6488.TWO", row, {}, {}, None, NOW)
        self.assertEqual(v["market_cap"], 200000000)
        self.assertEqual(v["status"], "reported")
        self.assertFalse(v["estimated"])
        row["MarketValue"] = "200000000"
        self.assertIsNone(taiwan_valuation("6488.TWO", row, {}, {}, None, NOW)["market_cap"])

    def test_us_wrong_symbol_currency_and_adr_are_rejected(self):
        summary = {"data": {"symbol": "AAPL", "assetClass": "STOCKS", "summaryData": {"MarketCap": {"value": "999"}}}}
        self.assertIsNone(us_valuation("MU", summary, {}, {}, None, NOW)["market_cap"])
        for financial in [financials(symbol="AAPL"), financials(currency="TWD")]:
            self.assertIsNone(us_valuation("MU", {}, financial, price("MU", "USD"), None, NOW)["trailing_pe"])
        adr = {**price("TSM", "USD"), "name": "Taiwan Semiconductor Manufacturing Company Limited ADR"}
        self.assertIsNone(us_valuation("TSM", {}, financials(symbol="TSM"), adr, None, NOW)["trailing_pe"])

    def test_latest_loss_does_not_select_older_positive_eps(self):
        data = financials(5)
        data["timeseries"]["result"][0]["trailingDilutedEPS"].append(
            {"asOfDate": "2026-08-31", "periodType": "TTM", "currencyCode": "USD", "reportedValue": {"raw": -2}})
        self.assertIsNone(us_valuation("MU", {}, data, price("MU", "USD"), None, NOW)["trailing_pe"])
        data = financials(0)
        self.assertIsNone(us_valuation("MU", {}, data, price("MU", "USD"), None, NOW)["trailing_pe"])

    def test_estimate_does_not_use_old_price_or_future_financial(self):
        old = {**price("MU", "USD"), "as_of": "2025-10-08T00:00:00Z"}
        self.assertIsNone(us_valuation("MU", {}, financials(), old, None, NOW)["trailing_pe"])
        data = financials()
        data["timeseries"]["result"][0]["trailingDilutedEPS"][0]["asOfDate"] = "2026-10-10"
        self.assertIsNone(us_valuation("MU", {}, data, price("MU", "USD"), None, NOW)["trailing_pe"])

    def test_daily_inputs_have_long_ttl(self):
        self.assertEqual(http_ttl("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", {}), 21600)
        self.assertEqual(http_ttl("https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/MU", {}), 21600)

    def test_two_tickers_share_one_official_download(self):
        live.HTTP_GET_CACHE.clear()
        def get(url, **kwargs):
            if "t187ap03" in url:
                return response([{"公司代號": code, "出表日期": "1151010", "已發行普通股數或TDR原股發行股數": "1000"} for code in ["2330", "3481"]])
            if "BWIBBU" in url:
                return response([{"Code": code, "Date": "1151008", "PEratio": "20"} for code in ["2330", "3481"]])
            return response({"chart": {"result": [{"meta": {"symbol": url.rsplit("/", 1)[-1], "currency": "TWD",
                "regularMarketPrice": 100, "regularMarketTime": NOW - 3 * 86400}}]}})
        with patch.object(live, "_ORIGINAL_REQUESTS_GET", side_effect=get) as fetch, patch("time.time", return_value=NOW):
            for symbol in ["2330.TW", "3481.TW"]:
                self.assertEqual(public_valuation(symbol)["market_cap"], 100000)
        self.assertEqual(sum("t187ap03" in call.args[0] for call in fetch.call_args_list), 1)
        self.assertEqual(sum("BWIBBU" in call.args[0] for call in fetch.call_args_list), 1)
        self.assertEqual(fetch.call_count, 4)
        live.HTTP_GET_CACHE.clear()

    def test_price_endpoint_rejects_mismatch_and_invalid_timestamp(self):
        for meta in [{"symbol": "AAPL"}, {"symbol": "MU", "currency": "USD", "regularMarketPrice": 100, "regularMarketTime": 1}]:
            with patch("openkiri_valuation.public_json", return_value=({"chart": {"result": [{"meta": meta}]}}, None)):
                self.assertEqual(valuation_price("MU"), {})


if __name__ == "__main__":
    unittest.main()
