import unittest

from openkiri_cache_policy import analyze_ttl, cache_prune, freeze_params, history_ttl, http_ttl


class CachePolicyTests(unittest.TestCase):
    def test_freeze_params_is_order_independent(self) -> None:
        left = freeze_params({"interval": "1m", "period": "1d"})
        right = freeze_params({"period": "1d", "interval": "1m"})
        self.assertEqual(left, right)

    def test_http_ttl_distinguishes_live_and_daily_history(self) -> None:
        chart_url = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL"
        self.assertEqual(http_ttl(chart_url, {"interval": "1m"}), 75)
        self.assertEqual(http_ttl(chart_url, {"interval": "1d"}), 900)

    def test_http_ttl_uses_upstream_specific_windows(self) -> None:
        self.assertEqual(http_ttl("https://query1.finance.yahoo.com/v7/finance/quote", None), 45)
        self.assertEqual(http_ttl("https://news.google.com/rss/search", None), 1800)
        self.assertEqual(http_ttl("https://example.com/data", None), 300)
        self.assertEqual(http_ttl("local-cache-key", None), 0)

    def test_history_and_analysis_ttls_prefer_fresher_intraday_data(self) -> None:
        self.assertEqual(history_ttl("1d", "5m"), 75)
        self.assertEqual(history_ttl("1y", "1d"), 900)
        self.assertEqual(analyze_ttl("1d", "15m"), 45)
        self.assertEqual(analyze_ttl("1y", "15m"), 120)
        self.assertEqual(analyze_ttl("1y", "1d"), 600)

    def test_cache_prune_removes_oldest_records(self) -> None:
        cache = {
            "oldest": (1.0, "a"),
            "middle": (2.0, "b"),
            "newest": (3.0, "c"),
        }
        cache_prune(cache, 2)
        self.assertEqual(set(cache), {"middle", "newest"})


if __name__ == "__main__":
    unittest.main()
