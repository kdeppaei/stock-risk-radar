import unittest
from datetime import datetime
from unittest.mock import patch

import openkiri_live as live
from openkiri_session import analysis_status, market_status, observation_status


def epoch(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def session(date="2026-10-08", zone="Asia/Taipei"):
    if zone == "America/New_York":
        windows = [("pre", "04:00:00-04:00", "09:30:00-04:00"),
                   ("regular", "09:30:00-04:00", "16:00:00-04:00"),
                   ("post", "16:00:00-04:00", "20:00:00-04:00")]
        offset = -14400
    else:
        windows = [("regular", "09:00:00+08:00", "13:30:00+08:00")]
        offset = 28800
    return {"exchange_timezone": zone, "sessions": {
        key: {"start": epoch(f"{date}T{start}"), "end": epoch(f"{date}T{end}"), "gmtoffset": offset}
        for key, start, end in windows}}


class SessionTests(unittest.TestCase):
    def test_taiwan_regular_open_and_closed_boundaries(self):
        meta = session()
        self.assertEqual(market_status(meta, epoch("2026-10-08T09:00:00+08:00"))["state"], "regular")
        after = market_status(meta, epoch("2026-10-08T13:30:00+08:00"))
        self.assertEqual(after["state"], "closed")
        self.assertEqual(after["basis"], "outside_provider_session")
        self.assertEqual(market_status(meta, epoch("2026-10-08T08:30:00+08:00"))["state"], "closed")

    def test_unknown_holiday_does_not_assume_weekday_open(self):
        market = market_status(session(), epoch("2026-10-09T10:00:00+08:00"))
        self.assertEqual(market["state"], "unknown")
        self.assertEqual(market["basis"], "unconfirmed")

    def test_weekend_uses_exchange_zone_not_server_zone(self):
        now = epoch("2026-10-10T02:00:00Z")
        self.assertEqual(market_status(session(), now)["state"], "closed")
        self.assertEqual(market_status(session("2026-10-09", "America/New_York"), now)["state"], "closed")
        self.assertEqual(market_status(session(), now)["basis"], "regular_weekend")

    def test_us_extended_sessions_and_provider_boundaries(self):
        meta = session("2026-10-09", "America/New_York")
        for hour, state in [("08:00", "pre"), ("09:30", "regular"), ("16:00", "post"), ("20:00", "closed")]:
            self.assertEqual(market_status(meta, epoch(f"2026-10-09T{hour}:00-04:00"))["state"], state)

    def test_epochs_support_early_close_and_dst_without_fixed_hours(self):
        meta = session("2026-11-27", "America/New_York")
        meta["sessions"] = {"regular": {"start": epoch("2026-11-27T09:30:00-05:00"),
            "end": epoch("2026-11-27T13:00:00-05:00"), "gmtoffset": -18000}}
        status = market_status(meta, epoch("2026-11-27T12:59:00-05:00"))
        self.assertEqual(status["state"], "regular")
        self.assertEqual(status["valid_until"], "2026-11-27T18:00:00+00:00")
        self.assertEqual(market_status(meta, epoch("2026-11-27T13:00:00-05:00"))["state"], "closed")

    def test_provider_market_state_requires_recent_fetch(self):
        now = epoch("2026-10-08T10:00:00+08:00")
        self.assertEqual(market_status({"market_state": "CLOSED", "fetched_at": now - 20}, now)["state"], "closed")
        self.assertEqual(market_status({"market_state": "REGULAR", "fetched_at": now - 600}, now)["state"], "unknown")
        self.assertEqual(market_status({"market_state": "REGULAR", "fetched_at": now + 600}, now)["state"], "unknown")

    def test_conflicting_provider_state_is_not_claimed_open(self):
        now = epoch("2026-10-08T14:00:00+08:00")
        status = market_status({**session(), "market_state": "REGULAR", "fetched_at": now}, now)
        self.assertEqual(status["state"], "unknown")
        self.assertEqual(status["basis"], "conflicting_source")

    def test_last_session_close_is_not_intraday_delay(self):
        now = epoch("2026-10-11T14:00:00+08:00")
        market = market_status(session(), now)
        quote = observation_status({"as_of": "2026-10-08T13:30:14+08:00"}, market, now)
        chart = observation_status({"as_of": "2026-10-08T09:00:00+08:00", "time_basis": "bar_start", "interval": "1d"}, market, now)
        self.assertEqual(quote["state"], "last_session")
        self.assertEqual(chart["state"], "last_session")
        self.assertFalse(quote["realtime_verified"])

    def test_old_quote_during_regular_session_is_delayed(self):
        now = epoch("2026-10-08T10:00:00+08:00")
        market = market_status(session(), now)
        self.assertEqual(observation_status({"as_of": now - 1200}, market, now)["state"], "delayed")
        self.assertEqual(observation_status({"as_of": now - 60}, market, now)["state"], "recent_observation")
        self.assertEqual(observation_status({"as_of": now + 3600}, market, now)["state"], "future_time")
        self.assertEqual(observation_status({}, market, now)["state"], "missing_time")

    def test_old_daily_bar_is_not_compared_to_one_minute_quote(self):
        now = epoch("2026-10-08T13:00:00+08:00")
        market = market_status(session(), now)
        chart = observation_status({"as_of": "2026-10-08T09:00:00+08:00", "interval": "1d", "time_basis": "bar_start"}, market, now)
        self.assertEqual(chart["state"], "historical_bar")
        old = observation_status({"as_of": now - 20 * 86400, "interval": "1d", "time_basis": "bar_start"}, market, now)
        self.assertEqual(old["state"], "older_data")

    def test_regular_quote_does_not_masquerade_as_extended_hours_quote(self):
        now = epoch("2026-10-09T17:00:00-04:00")
        market = market_status(session("2026-10-09", "America/New_York"), now)
        self.assertEqual(observation_status({"as_of": now - 3600, "quote_session": "regular"}, market, now)["state"], "regular_session_only")

    def test_intraday_daily_fallback_is_explicit(self):
        status = analysis_status({"requested_interval": "5m", "interval": "1d", "provenance": {}}, epoch("2026-10-11T00:00:00Z"))
        self.assertEqual(status["chart"]["state"], "daily_fallback")

    def test_cached_analysis_recalculates_state_without_upstream_request(self):
        now = epoch("2026-10-08T13:29:59+08:00")
        payload = {"provenance": {"price": session(), "chart": {}}, "interval": "1d"}
        key = ("2330.TW", "1y", "1d", False)
        live.ANALYZE_CACHE[key] = (now - 10, payload)
        with patch("time.time", return_value=now + 2), patch.object(live.base, "fetch_price_history", side_effect=AssertionError("extra request")):
            result = live.analyze("2330.TW", "1y", "1d", False)
        self.assertEqual(result["data_status"]["market"]["state"], "closed")
        self.assertNotIn("data_status", payload)
        live.ANALYZE_CACHE.clear()

    def test_malformed_session_metadata_is_safe(self):
        for meta in [{"sessions": []}, {"sessions": {"regular": "bad"}}, {"sessions": {"regular": {"start": 1, "end": 99999999}}}]:
            self.assertEqual(market_status(meta, 100)["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
