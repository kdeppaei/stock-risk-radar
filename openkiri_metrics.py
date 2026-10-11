"""Bounded in-memory telemetry. Never retain URLs, queries, cookies, or user data."""
from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import urlsplit


def provider_group(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(".yahoo.com"):
        path = urlsplit(url).path.lower()
        if "/chart/" in path:
            return "Yahoo chart"
        if "quotesummary" in path or "getcrumb" in path or host == "fc.yahoo.com":
            return "Yahoo fundamentals/auth"
        if "finance/quote" in path:
            return "Yahoo quote"
        if "fundamentals-timeseries" in path:
            return "Yahoo financial statements"
        return "Yahoo other"
    if host == "openapi.twse.com.tw":
        return "TWSE official"
    if host == "www.tpex.org.tw":
        return "TPEx official"
    if host == "api.nasdaq.com":
        return "Nasdaq valuation"
    if host in {"www.google.com", "google.com"}:
        return "Google Finance"
    if host == "news.google.com":
        return "Google News"
    return "Other providers"


class Metrics:
    def __init__(self):
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.lock = Lock()
        self.groups = {}

    def record(self, name, *, elapsed=0, status=200, sent=0, received=0, hit=False):
        with self.lock:
            if name not in self.groups and len(self.groups) >= 64:
                name = "other"
            row = self.groups.setdefault(name, {"requests": 0, "cache_hits": 0, "errors": 0,
                "sent_bytes": 0, "received_bytes": 0, "latency_total_ms": 0, "latencies": deque(maxlen=128)})
            row["requests"] += 1
            row["cache_hits"] += int(hit)
            row["errors"] += int(status >= 400)
            row["sent_bytes"] += max(0, int(sent))
            row["received_bytes"] += max(0, int(received))
            ms = max(0, elapsed * 1000)
            row["latency_total_ms"] += ms
            row["latencies"].append(ms)

    def snapshot(self):
        with self.lock:
            rows = []
            for name, row in sorted(self.groups.items()):
                samples = sorted(row["latencies"])
                rows.append({"name": name, **{k: v for k, v in row.items() if k not in {"latencies", "latency_total_ms"}},
                    "avg_ms": round(row["latency_total_ms"] / row["requests"], 1),
                    "p95_ms": round(samples[max(0, math.ceil(len(samples) * .95) - 1)], 1),
                    "cache_hit_pct": round(row["cache_hits"] / row["requests"] * 100, 1) if name.startswith("Cache ") else None})
        return {"ok": True, "since": self.started_at, "scope": "process_lifetime", "rows": rows,
                "measurement": "API body bytes are after compression. Upstream sent bytes estimate HTTP request bytes; received bytes are decoded payload sizes, not billed egress. Excludes TLS, headers on API responses, streaming bodies, and platform traffic. Resets on restart; not a Render billing meter."}


METRICS = Metrics()


class MetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started, size, status = time.monotonic(), 0, 500

        async def measured_send(message):
            nonlocal size, status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                size += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, measured_send)
        finally:
            route = getattr(scope.get("route"), "path", "other")
            if not route.startswith("/api/"):
                route = "static/page"
            METRICS.record("API " + route, elapsed=time.monotonic() - started, status=status, sent=size)
