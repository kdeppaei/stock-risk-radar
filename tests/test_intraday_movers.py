import unittest
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

import openkiri_live as live
from openkiri_data import iso_time
from openkiri_session import intraday_status
from test_session_status import epoch, session


NOW = epoch("2026-10-08T10:00:00+08:00")


def row(symbol="2330.TW", score=12, **meta):
    return {"symbol": symbol, "name": symbol, "market": "TW", "price": 100,
            "change_pct": score / 8, "volume": 0, "pattern": "", "pattern_score": 0,
            "source": "1d/5m", "model_score": score,
            "side": "long" if score >= 8 else "short" if score <= -8 else "watch",
            "provenance": {**session(), "as_of": NOW - 60, "fetched_at": NOW - 10,
                           "interval": "5m", "time_basis": "bar_start", **meta}}


class IntradayMoversTests(unittest.TestCase):
    def setUp(self):
        live.base.MOVERS_CACHE.clear()

    def tearDown(self):
        live.base.MOVERS_CACHE.clear()

    def pack(self, rows, now=NOW):
        return live.movers_payload(rows, {"TW"}, 6, "live", iso_time(NOW), now)

    def test_only_current_session_intraday_rows_are_candidates(self):
        raw = [row("LONG", 8), row("SHORT", -8), row("WATCH", 7),
               row("DELAY", as_of=NOW - 1200), row("DAILY", interval="1d"),
               row("UNKNOWN", sessions={})]
        original = deepcopy(raw)
        result = self.pack(raw)
        self.assertEqual([r["symbol"] for r in result["long_candidates"]], ["LONG"])
        self.assertEqual([r["symbol"] for r in result["short_candidates"]], ["SHORT"])
        self.assertEqual(result["counts"], {"active": 3, "reference": 0, "excluded": 3})
        self.assertEqual(result["excluded_reasons"], {"delayed": 1, "daily_fallback": 1, "market_unconfirmed": 1})
        self.assertEqual(raw, original)
        self.assertNotIn("provenance", result["long_candidates"][0])

    def test_future_missing_and_pre_session_observations_are_excluded(self):
        for stamp, reason in [(None, "missing_time"), (NOW + 30, "future_time"), (NOW + 300, "future_time")]:
            with self.subTest(reason=reason, stamp=stamp):
                self.assertEqual(intraday_status(row(as_of=stamp)["provenance"], NOW)["reason"], reason)
        opening = epoch("2026-10-08T09:01:00+08:00")
        status = intraday_status(row(as_of=opening - 90)["provenance"], opening)
        self.assertEqual(status["price"]["state"], "recent_observation")
        self.assertEqual(status["reason"], "outside_session")
        self.assertEqual(status["eligibility"], "excluded")

    def test_explicit_open_without_session_window_is_not_enough(self):
        meta = row(sessions={}, market_state="REGULAR", fetched_at=NOW)["provenance"]
        self.assertEqual(intraday_status(meta, NOW)["reason"], "missing_session")

    def test_closed_last_session_is_reference_not_active(self):
        close = epoch("2026-10-08T13:30:00+08:00")
        raw = [row("LAST", as_of=close - 300),
               row("DAILY", as_of=epoch("2026-10-08T09:00:00+08:00"), interval="1d"),
               row("OLD", as_of=close - 86400)]
        result = self.pack(raw, epoch("2026-10-11T14:00:00+08:00"))
        self.assertEqual(result["long_candidates"], [])
        self.assertEqual({r["symbol"] for r in result["reference_long"]}, {"LAST", "DAILY"})
        self.assertEqual(result["counts"]["excluded"], 1)
        self.assertEqual(result["reference_long"][0]["as_of"], iso_time(close - 300))

    def test_very_old_closed_data_stays_excluded(self):
        now = epoch("2026-10-18T14:00:00+08:00")
        result = self.pack([row(as_of=epoch("2026-10-08T13:25:00+08:00"))], now)
        self.assertEqual(result["reference_long"], [])
        self.assertEqual(result["counts"]["excluded"], 1)

    def test_us_pre_and_post_market_are_not_regular_candidates(self):
        for at in ["2026-10-09T08:00:00-04:00", "2026-10-09T17:00:00-04:00"]:
            now = epoch(at)
            meta = {**session("2026-10-09", "America/New_York"), "as_of": now - 60, "interval": "5m"}
            self.assertEqual(intraday_status(meta, now)["reason"], "outside_regular")

    def test_validity_ends_at_session_boundary_or_data_age_limit(self):
        meta = row(as_of=NOW - 899)["provenance"]
        status = intraday_status(meta, NOW)
        self.assertEqual(status["valid_until"], iso_time(NOW + 1))
        self.assertEqual(intraday_status(meta, NOW + 2)["eligibility"], "excluded")
        close = epoch("2026-10-08T13:30:00+08:00")
        status = intraday_status(row(as_of=close - 300)["provenance"], close - 1)
        self.assertEqual(status["valid_until"], iso_time(close))

    def test_cached_rows_reclassify_at_close_without_network_or_time_rewrite(self):
        close = epoch("2026-10-08T13:30:00+08:00")
        raw = row(as_of=close - 300)
        key = ("TW", 6, "live")
        saved = {"rows": [raw], "updated_at": iso_time(close - 10)}
        live.base.MOVERS_CACHE[key] = (close - 10, saved)
        with patch("time.time", return_value=close - 1), patch.object(live.base, "mover_quote", side_effect=AssertionError("extra scan")):
            before = live.movers("TW", 6, "live")
        with patch("time.time", return_value=close + 1), patch.object(live.base, "mover_quote", side_effect=AssertionError("extra scan")):
            after = live.movers("TW", 6, "live")
        self.assertEqual(len(before["long_candidates"]), 1)
        self.assertEqual(after["long_candidates"], [])
        self.assertEqual(len(after["reference_long"]), 1)
        self.assertEqual(after["updated_at"], before["updated_at"])
        self.assertEqual(after["reference_long"][0]["as_of"], before["long_candidates"][0]["as_of"])
        self.assertNotIn("data_status", saved["rows"][0])

    def test_route_scores_thresholds_and_cache_uses_no_extra_scan(self):
        raw = [row("LONG", 8), row("SHORT", -8), row("WATCH", 4)]
        with patch("time.time", return_value=NOW), patch.object(live, "saver_universe", return_value=raw), \
                patch.object(live.base, "mover_quote", side_effect=lambda item, mode: dict(item)) as fetch:
            result = live.movers("TW", 6, "live")
            cached = live.movers("TW", 6, "live")
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual([r["symbol"] for r in result["long_candidates"]], ["LONG"])
        self.assertEqual([r["symbol"] for r in result["short_candidates"]], ["SHORT"])
        self.assertEqual(result, cached)

    def test_history_metadata_is_retained_without_an_extra_quote(self):
        stamp = datetime.fromtimestamp(NOW - 60, timezone.utc)
        history = [{"date": stamp, "close": 99, "volume": 10},
                   {"date": stamp, "close": 100, "volume": 20, "_meta": {
                       "fetched_at": NOW - 10, "exchangeTimezoneName": "Asia/Taipei",
                       "currentTradingPeriod": session()["sessions"]}}]
        item = {"symbol": "2330.TW", "name": "TSMC", "market": "TW", "industry": "semiconductor"}
        with patch.object(live.base, "fetch_price_history", return_value=history) as fetch:
            result = live.base.mover_quote(item, "live")
        fetch.assert_called_once_with("2330.TW", "1d", "5m")
        self.assertEqual(result["provenance"]["as_of"], stamp)
        self.assertEqual(result["provenance"]["interval"], "5m")
        self.assertEqual(result["provenance"]["fetched_at"], NOW - 10)
        self.assertEqual(result["provenance"]["sessions"], session()["sessions"])

    def test_daily_fallback_metadata_never_claims_five_minutes(self):
        history = [{"close": 99, "volume": 10}, {"close": 100, "volume": 20}]
        item = {"symbol": "2330.TW", "name": "TSMC", "market": "TW", "industry": "semiconductor"}
        with patch.object(live.base, "fetch_price_history", side_effect=[[], history]) as fetch:
            result = live.base.mover_quote(item, "live")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["provenance"]["interval"], "1d")


if __name__ == "__main__":
    unittest.main()
