import unittest
from unittest.mock import patch

import openkiri_live
from openkiri_integrity import SnapshotFingerprintTracker


def market_rows(close: float) -> list[dict[str, object]]:
    return [
        {"date_label": "2026-08-21", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        {"date_label": "2026-08-22", "open": 100.0, "high": close + 1, "low": 99.5, "close": close, "volume": 1200},
    ]


class AnalyzeIntegrityIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        openkiri_live.ANALYZE_CACHE.clear()

    def test_analyze_exposes_sha256_metadata_and_detects_change(self) -> None:
        current_rows = market_rows(102.0)
        tracker = SnapshotFingerprintTracker()

        with (
            patch.object(openkiri_live, "SNAPSHOT_FINGERPRINTS", tracker),
            patch.object(openkiri_live, "minimum_history_bars", return_value=2),
            patch.object(openkiri_live.base, "normalize_symbol", return_value=("MU", "US")),
            patch.object(openkiri_live.base, "candidate_symbols", return_value=["MU"]),
            patch.object(openkiri_live.base, "fetch_price_history", side_effect=lambda *_: [dict(row) for row in current_rows]),
            patch.object(openkiri_live.base, "calculate_indicators", side_effect=lambda rows: rows),
            patch.object(openkiri_live.base, "support_resistance", return_value={}),
            patch.object(openkiri_live.base, "build_risk", return_value={}),
            patch.object(openkiri_live.base, "build_suitability", return_value={}),
            patch.object(openkiri_live.base, "build_prediction", return_value={}),
            patch.object(openkiri_live.base, "build_chart_math", return_value={}),
            patch.object(openkiri_live.base, "build_design_signals", return_value={}),
            patch.object(openkiri_live.base, "find_universe_item", return_value={"symbol": "MU", "market": "US"}),
            patch.object(openkiri_live.base, "technical_payload", return_value={}),
            patch.object(openkiri_live.base, "build_chart_rows", side_effect=lambda rows, _market: rows),
            patch.object(openkiri_live, "fast_valuation_placeholder", return_value={}),
        ):
            first = openkiri_live.analyze(symbol="MU", period="1d", interval="5m")
            self.assertEqual(first["integrity"]["algorithm"], "sha256")
            self.assertEqual(len(first["integrity"]["fingerprint"]), 64)
            self.assertIsNone(first["integrity"]["changed_from_previous"])

            openkiri_live.ANALYZE_CACHE.clear()
            current_rows[-1]["close"] = 103.0
            changed = openkiri_live.analyze(symbol="MU", period="1d", interval="5m")
            self.assertTrue(changed["integrity"]["changed_from_previous"])
            self.assertNotEqual(first["integrity"]["fingerprint"], changed["integrity"]["fingerprint"])


if __name__ == "__main__":
    unittest.main()
