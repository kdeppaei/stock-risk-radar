import unittest

from openkiri_integrity import (
    SnapshotFingerprintTracker,
    canonical_json,
    fingerprint_payload,
    looks_like_32_hex,
    md5_hex,
    sha256_hex,
)


class HashInspectorTests(unittest.TestCase):
    def test_lowercase_32_hex_has_digest_shape(self) -> None:
        self.assertTrue(looks_like_32_hex("098f6bcd4621d373cade4e832627b4f6"))

    def test_uppercase_32_hex_has_digest_shape(self) -> None:
        self.assertTrue(looks_like_32_hex("098F6BCD4621D373CADE4E832627B4F6"))

    def test_non_hex_or_wrong_length_does_not_match(self) -> None:
        self.assertFalse(looks_like_32_hex("not-an-md5-value"))
        self.assertFalse(looks_like_32_hex("a" * 31))

    def test_local_md5_demo_matches_known_value(self) -> None:
        self.assertEqual(md5_hex("test"), "098f6bcd4621d373cade4e832627b4f6")

    def test_local_sha256_demo_matches_known_value(self) -> None:
        self.assertEqual(
            sha256_hex("test"),
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        )


class SnapshotIntegrityTests(unittest.TestCase):
    def test_canonical_json_is_independent_of_mapping_order(self) -> None:
        left = canonical_json({"symbol": "MU", "latest": {"close": 125.0, "volume": 10}})
        right = canonical_json({"latest": {"volume": 10, "close": 125.0}, "symbol": "MU"})
        self.assertEqual(left, right)

    def test_non_finite_numbers_are_normalized(self) -> None:
        self.assertEqual(canonical_json({"value": float("nan")}), '{"value":null}')

    def test_payload_fingerprint_changes_with_data(self) -> None:
        first, _ = fingerprint_payload({"close": 125.0})
        second, _ = fingerprint_payload({"close": 126.0})
        self.assertNotEqual(first, second)

    def test_tracker_reports_first_observation_then_stable_snapshot(self) -> None:
        tracker = SnapshotFingerprintTracker()
        key = ("MU", "1y", "1d")
        first = tracker.observe(key, {"close": 125.0})
        second = tracker.observe(key, {"close": 125.0})
        self.assertIsNone(first["changed_from_previous"])
        self.assertFalse(second["changed_from_previous"])
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_tracker_reports_changed_snapshot(self) -> None:
        tracker = SnapshotFingerprintTracker()
        key = ("MU", "1y", "1d")
        tracker.observe(key, {"close": 125.0})
        changed = tracker.observe(key, {"close": 126.0})
        self.assertTrue(changed["changed_from_previous"])


if __name__ == "__main__":
    unittest.main()
