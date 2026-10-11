const { test, before, after } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
let server, browser, origin;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Synthetic prices keep layout and query-race checks independent of providers.
function analysis(symbol = "2330.TW") {
  const price = symbol === "MU" ? 150 : 100;
  const chart = Array.from({ length: 64 }, (_, i) => {
    const close = price - 20 + i * 0.3 + Math.sin(i) * 2;
    return { date: new Date(Date.UTC(2026, 0, i + 1)).toISOString().slice(0, 10), open: close - 1, close, high: close + 2, low: close - 2, volume: 100000 + i * 1000, ma5: close - 1, ma20: close - 3, ma25: close - 4, ma60: close - 6, bias25: 4, color: i % 3 ? "#51cc96" : "#fa7e87" };
  });
  const score = { label: "watch", score: 60 };
  return { ok: true, symbol, market: symbol.endsWith(".TW") ? "TW" : "US", latest: { close: price, date: "2026-03-05" }, change: { pct: 1.25 }, risk: { score: 35, trend: "bullish" }, prediction: { bias: "bullish", confidence: 65, drivers: [], model: "fixture", advice: "fixture" }, technical: { ma5: 98, ma20: 96, ma25: 95, ma60: 90, bias25_pct: 4, rsi14: 50 }, levels: { breakout_call: 104, resistance_60d: 103, backtest_zone: 95, stop_loss_reference: 90 }, suitability: { intraday: score, short_term: score, long_term: score }, macro: { label: "neutral", items: {} }, news: { label: "neutral", items: [], positive: 0, negative: 0 }, valuation: { market_cap: 31000000000000, currency: "TWD", trailing_pe: 24 }, related: [], chart_math: { crosses: [], confidence: 65 }, chart };
}

before(async () => {
  server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    const relative = url.pathname === "/" ? "templates/index.html" : url.pathname.slice(1);
    const file = path.resolve(root, relative);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404).end(); return; }
    const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
    res.setHeader("Content-Type", `${types[path.extname(file)] || "text/plain"}; charset=utf-8`);
    let content = fs.readFileSync(file);
    if (url.pathname === "/") content = content.toString().replace("</body>", '<script src="/static/openkiri-live.js"></script></body>');
    res.end(content);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
});
after(async () => { await browser?.close(); await new Promise((resolve) => server?.close(resolve)); });

async function pageFor(width = 1440, locale = "zh-tw") {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const errors = [], requests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript((value) => localStorage.setItem("srr.lang", value), locale);
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    requests.push(url.pathname);
    let data = { ok: true, rows: [], items: [], gainers: [], losers: [], industries: [], topics: [], setups: [], scanned: 0 };
    if (url.pathname === "/api/analyze") {
      const symbol = url.searchParams.get("symbol");
      if (symbol === "SLOW") await pause(800);
      if (symbol === "FAIL") return route.fulfill({ status: 503, json: { ok: false, detail: "Test provider outage" } });
      data = analysis(/^\d+$/.test(symbol) ? `${symbol}.TW` : symbol);
      data.interval = url.searchParams.get("interval");
      data.period = url.searchParams.get("period");
      data.analysis_scope = url.searchParams.get("context") === "true" ? "technical_news_events" : "technical_only";
      const meta = { source: "Yahoo chart", currency: data.market === "TW" ? "TWD" : "USD", as_of: "2026-03-05T14:30:00+00:00", fetched_at: "2026-03-05T14:35:00+00:00" };
      data.provenance = { chart: meta, price: meta };
      if (symbol?.startsWith("SESSION")) {
        const now = Date.now();
        const market = { state: "regular", basis: "provider_session", checked_at: new Date(now).toISOString(), valid_until: new Date(now + 120000).toISOString(), timezone: "America/New_York" };
        const quality = { state: "recent_observation", as_of: new Date(now - 30000).toISOString(), interval: "5m", stale_after_seconds: 900 };
        data.data_status = { market, price: { ...quality }, chart: { ...quality } };
        if (symbol === "SESSIONLIVE") Object.assign(data.data_status.price, { state: "delayed", as_of: new Date(now - 1800000).toISOString() });
        if (symbol === "SESSIONCLOSED") {
          Object.assign(market, { state: "closed", basis: "regular_weekend" });
          data.data_status.price.state = data.data_status.chart.state = "last_session";
        }
        if (symbol === "SESSIONUNKNOWN") market.state = "unknown";
        if (symbol === "SESSIONEXPIRED") market.valid_until = new Date(now - 1000).toISOString();
        if (symbol === "SESSIONFALLBACK") {
          data.interval = "1d"; data.data_fallback = true; data.data_status.chart.state = "daily_fallback";
        }
        if (symbol === "SESSIONFUTURE") data.data_status.price.state = "future_time";
        if (symbol === "SESSIONPOST") { market.state = "post"; data.data_status.price.state = "regular_session_only"; }
      }
      if (symbol === "EMPTY") data.chart = [];
      if (symbol === "WRONG") data.symbol = "2330.TW";
    } else if (url.pathname === "/api/valuation") {
      const symbol = url.searchParams.get("symbol");
      if (symbol === "SLOWVAL") await pause(700);
      data = { ok: true, symbol, valuation: { symbol, market_cap: symbol === "NOVAL" ? null : symbol === "MU" ? 999000000 : 31000000000000, currency: symbol.endsWith(".TW") ? "TWD" : "USD", trailing_pe: 24, status: symbol === "NOVAL" ? "unavailable" : "reported", source: "Yahoo quote", as_of: null, fetched_at: "2026-03-05T14:35:00Z", market_cap_change_pct: 88 } };
      if (symbol === "ESTIMATE") Object.assign(data.valuation, { status: "derived", source: "TWSE", fallback: true, fields: {
        market_cap: { source: "TWSE + Yahoo chart", estimated: true, as_of: "2026-03-05T05:30:00Z", shares_as_of: "2026-03-01T00:00:00Z" },
        trailing_pe: { source: "TWSE", estimated: false, as_of: "2026-03-05T00:00:00Z" }
      } });
      if (symbol === "MIXED") Object.assign(data.valuation, { status: "derived", source: "Nasdaq", fields: {
        market_cap: { source: "Nasdaq", estimated: false, as_of: null },
        trailing_pe: { source: "Yahoo fundamentals-timeseries + Yahoo chart", estimated: true, eps_ttm: 4, as_of: "2026-02-28T00:00:00Z", price_as_of: "2026-03-05T14:30:00Z" }
      } });
    } else if (url.pathname === "/api/movers") data = { ok: true, markets: url.searchParams.get("markets").split(","), scanned: 0, counts: { active: 0, reference: 0, excluded: 0 }, long_candidates: [], short_candidates: [], reference_long: [], reference_short: [] };
    else if (url.pathname === "/api/diagnostics") data = { ok: true, since: "2026-03-05T00:00:00Z", rows: [{ name: "API /api/analyze", requests: 4, cache_hit_pct: 50, avg_ms: 20, p95_ms: 30, sent_bytes: 4096, received_bytes: 0, errors: 0 }] };
    else if (url.pathname === "/api/market-context") data = { usd_twd: {}, macro: { label: "neutral" } };
    else if (url.pathname.startsWith("/api/quote/")) data = { ok: true, symbol: url.pathname.split("/").at(-1), price: 100, change_pct: 1, source: "fixture" };
    else if (url.pathname.startsWith("/api/daytrade/")) data = { ok: false, detail: "Fixture: unavailable" };
    await route.fulfill({ json: data });
  });
  await page.goto(origin);
  await page.waitForFunction(() => document.getElementById("price").textContent === "100");
  return { page, errors, requests };
}

test("responsive workspace, keyboard chart, and on-demand panels", async () => {
  for (const width of [320, 390, 768, 1440]) {
    const { page, errors, requests } = await pageFor(width);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `overflow at ${width}`);
    assert.equal(await page.locator(".workspace-panel").isVisible(), false);
    assert.equal(await page.locator("#chart").isVisible(), true);
    assert.equal(requests.some((url) => /movers|recommendations|event-alerts|screener|serenity/.test(url)), false);
    assert.equal(await page.locator(".alert-bell:visible").count(), 1);
    const chartPixels = await page.locator("#chart").evaluate((el) => {
      const rgba = el.getContext("2d").getImageData(0, 0, el.width, el.height).data;
      let count = 0;
      for (let i = 3; i < rgba.length; i += 4) if (rgba[i]) count++;
      return count;
    });
    assert.ok(chartPixels > 5000, "chart canvas is populated");
    await page.locator("#chart").focus();
    await page.keyboard.press("ArrowLeft");
    assert.match(await page.locator("#chartTooltip").innerText(), /2330.TW/);
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#chartTooltip").isVisible(), false);
    await page.locator(".indicator-menu summary").click();
    await page.locator('.chart-switch:has([data-chart-layer="ma25"])').click();
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("okiri.chartLayers")).ma25), false);
    await page.locator(".indicator-menu summary").focus();
    await page.keyboard.press("Escape");
    assert.equal(await page.locator(".indicator-menu").getAttribute("open"), null);
    await page.locator('[data-workspace-tab="screener"]').click();
    assert.equal(await page.locator("#pane-screener").isVisible(), true);
    assert.equal(await page.locator("#chart").isVisible(), false);
    assert.equal(requests.includes("/api/bots"), false);
    await page.locator('[data-workspace-tab="daytrade"]').click();
    await page.waitForFunction(() => document.getElementById("dtModelMeta").textContent !== "-");
    assert.equal(await page.locator("#dtMarketMode").count(), 1);
    assert.equal(await page.locator("#okiriDtMarket, #okiriDtModelBoard").count(), 0);
    assert.equal(requests.filter((url) => url === "/api/movers").length, 1);
    assert.equal(await page.locator("#dtCostBtn").isVisible(), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `T+0 overflow at ${width}`);
    await page.locator('[data-workspace-tab="overview"]').click();
    if (process.env.UI_SCREENSHOTS) {
      fs.mkdirSync(process.env.UI_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOTS, `overview-${width}.png`), fullPage: true });
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
});

function moverFixture(market = "TW", now = Date.now()) {
  const stamp = new Date(now - 60000).toISOString();
  const valid = new Date(now + 120000).toISOString();
  const long = { market, symbol: market === "TW" ? "2330.TW" : "MU", name: "Synthetic company with a longer name", pattern: "breakout", price: 100, change_pct: 2, model_score: 16, source: "1d/5m", interval: "5m", as_of: stamp,
    data_status: { eligibility: "active", valid_until: valid, market: { state: "regular" }, price: { state: "recent_observation", as_of: stamp, stale_after_seconds: 900 } } };
  const reference = structuredClone(long);
  Object.assign(reference, { symbol: market === "TW" ? "LAST.TW" : "LAST", interval: "1d", source: "5d/1d", as_of: "2026-10-08T01:00:00Z" });
  Object.assign(reference.data_status, { eligibility: "reference", market: { state: "closed" } });
  Object.assign(reference.data_status.price, { state: "last_session", as_of: reference.as_of });
  const counts = { active: 1, reference: 1, excluded: 5 };
  return { ok: true, mode: "live", markets: [market], scanned: 7, updated_at: new Date(now).toISOString(), counts,
    market_sessions: { [market]: { state: "regular", checked_at: new Date(now).toISOString(), valid_until: valid, scanned: 7, counts } },
    gainers: [{ symbol: "UNSAFE" }], losers: [], long_candidates: [long], short_candidates: [], reference_long: [reference], reference_short: [] };
}

function pollingFixture(markets, states, now) {
  const parts = markets.map((market) => {
    const data = moverFixture(market, now);
    const state = states[market] || "unknown";
    if (state !== "regular") data.long_candidates = [];
    if (state !== "closed") data.reference_long = [];
    data.counts = { active: data.long_candidates.length, reference: data.reference_long.length,
      excluded: 7 - data.long_candidates.length - data.reference_long.length };
    Object.assign(data.market_sessions[market], { state, counts: data.counts, valid_until: new Date(now + 300000).toISOString() });
    data.reference_long.forEach((row) => row.data_status.valid_until = new Date(now + 300000).toISOString());
    return data;
  });
  const data = { ok: true, mode: "live", markets, scanned: parts.length * 7, updated_at: new Date(now).toISOString(),
    market_sessions: Object.assign({}, ...parts.map((part) => part.market_sessions)), counts: { active: 0, reference: 0, excluded: 0 } };
  for (const key of Object.keys(data.counts)) data.counts[key] = parts.reduce((sum, part) => sum + part.counts[key], 0);
  for (const key of ["long_candidates", "short_candidates", "reference_long", "reference_short"]) data[key] = parts.flatMap((part) => part[key]);
  return data;
}

async function pollingPage(locale = "zh-tw", saver = true) {
  const result = await pageFor(390, locale);
  const { page } = result;
  await page.addInitScript((value) => localStorage.setItem("srr.dataSaver", value), saver ? "1" : "0");
  await page.clock.install({ time: new Date() });
  await page.reload();
  await page.waitForFunction(() => document.getElementById("price").textContent === "100");
  const calls = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (/^\/api\/(movers|quote\/|daytrade\/|tw\/market-pulse)/.test(url.pathname)) {
      calls.push({ path: url.pathname, markets: url.searchParams.get("markets"),
        symbol: request.method() === "POST" ? request.postDataJSON()?.symbol : url.pathname.split("/").at(-1) });
    }
  });
  return { ...result, calls };
}

async function settleDaytrade(page) {
  await page.waitForFunction(() => daytradeRequests.size === 0 && !document.getElementById("dtModelMeta").textContent.includes("..."));
}

async function setPageHidden(page, hidden) {
  await page.evaluate((value) => {
    Object.defineProperty(document, "hidden", { configurable: true, value });
    Object.defineProperty(document, "visibilityState", { configurable: true, value: value ? "hidden" : "visible" });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);
}

test("closed markets stay request-free after facts expire, then manual refresh resumes", async () => {
  const { page, errors, calls } = await pollingPage("en");
  let state = "closed";
  await page.route("**/api/movers?**", async (route) => {
    const now = await page.evaluate(() => Date.now());
    await route.fulfill({ json: pollingFixture(["TW"], { TW: state }, now) });
  });
  await page.locator('[data-workspace-tab="daytrade"]').click();
  await settleDaytrade(page);
  assert.equal(await page.locator("#dtModelCadence").getAttribute("data-state"), "closed");
  assert.match(await page.locator("#dtModelCadence").innerText(), /Paused: closed session/);
  assert.match(await page.locator("#dtModelCadence").getAttribute("title"), /TW.*Last session check/);
  for (const [locale, label] of [["zh-tw", "休市暫停"], ["zh-cn", "休市暂停"], ["en", "Paused: closed session"]]) {
    await page.locator(".language-menu summary").click();
    await page.locator(`[data-lang="${locale}"]`).click();
    assert.equal(await page.locator("#dtModelCadence").innerText(), label, "translation preserves paused status");
  }
  const before = calls.length;
  await page.clock.runFor(360000);
  assert.equal(calls.length, before, "all T+0 requests remain stopped after evidence expires");
  assert.match(await page.locator("#dtModelCadence").innerText(), /needs confirmation/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  state = "regular";
  await page.locator("#dtRefreshBtn").click();
  await settleDaytrade(page);
  assert.equal(await page.locator("#dtModelCadence").getAttribute("data-state"), "running");
  const probes = calls.filter((call) => call.path === "/api/movers").length;
  await page.clock.runFor(121000);
  await settleDaytrade(page);
  assert.equal(calls.filter((call) => call.path === "/api/movers").length, probes + 1);
  assert.deepEqual(errors, []);
  await page.close();
});

test("hidden and inactive workspaces abort batches, stay quiet, and probe once on return", async () => {
  const { page, errors, calls } = await pollingPage("zh-tw", false);
  let release;
  let holdQuotes = false;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/quote/**", async (route) => {
    if (holdQuotes) await held;
    await route.fulfill({ json: { ok: true, symbol: new URL(route.request().url()).pathname.split("/").at(-1), price: 100, change_pct: 1 } });
  });
  await page.route("**/api/movers?**", async (route) => {
    const now = await page.evaluate(() => Date.now());
    await route.fulfill({ json: pollingFixture(["TW"], { TW: "regular" }, now) });
  });
  await page.locator('[data-workspace-tab="daytrade"]').click();
  await settleDaytrade(page);
  holdQuotes = true;
  await page.locator("#dtRefreshBtn").click();
  await page.waitForFunction(() => daytradeRequests.has("quotes"));
  await setPageHidden(page, true);
  assert.equal(await page.evaluate(() => daytradeRequests.size), 0);
  const hiddenCalls = calls.length;
  release();
  await pause(100);
  await page.clock.runFor(60000);
  assert.equal(calls.length, hiddenCalls, "no detail requests follow aborted quotes and no hidden polling");
  holdQuotes = false;
  const probes = calls.filter((call) => call.path === "/api/movers").length;
  await setPageHidden(page, false);
  await settleDaytrade(page);
  assert.equal(calls.filter((call) => call.path === "/api/movers").length, probes + 1);
  await page.locator('[data-workspace-tab="overview"]').click();
  const inactiveCalls = calls.length;
  await page.clock.runFor(60000);
  assert.equal(calls.length, inactiveCalls, "overview has no T+0 polling");
  await page.locator('[data-workspace-tab="daytrade"]').click();
  await settleDaytrade(page);
  assert.equal(calls.filter((call) => call.path === "/api/movers").length, probes + 2);
  assert.deepEqual(errors, []);
  await page.close();
});

test("mixed-market polling skips closed markets and preserves their original reference times", async () => {
  const { page, errors, calls } = await pollingPage();
  await page.evaluate(() => { daytradeSymbols = ["2330.TW", "MU"]; daytradeMarketMode = "US,TW"; });
  await page.route("**/api/movers?**", async (route) => {
    const markets = new URL(route.request().url()).searchParams.get("markets").split(",");
    const now = await page.evaluate(() => Date.now());
    await route.fulfill({ json: pollingFixture(markets, { TW: "closed", US: "regular" }, now) });
  });
  await page.locator('[data-workspace-tab="daytrade"]').click();
  await settleDaytrade(page);
  await page.locator("#dtModelReference > summary").click();
  const sourceTime = await page.locator("#dtReferenceBoard .model-time").innerText();
  const twQuote = await page.locator('tr[data-quote-symbol="2330.TW"]').innerText();
  assert.match(await page.locator("#dtModelCadence").innerText(), /TW.*休市暫停/);
  const before = calls.length;
  await page.clock.runFor(121000);
  await settleDaytrade(page);
  assert.deepEqual(calls.slice(before).filter((call) => call.path === "/api/movers").map((call) => call.markets), ["US"]);
  assert.deepEqual(calls.slice(before).filter((call) => call.path.startsWith("/api/quote/")).map((call) => call.symbol), ["MU"]);
  assert.equal(await page.locator("#dtReferenceBoard .model-time").innerText(), sourceTime);
  assert.equal(await page.locator('tr[data-quote-symbol="2330.TW"]').innerText(), twQuote);
  assert.equal(await page.locator('#dtModelBoard [data-model-symbol="MU"]').count(), 1);
  assert.doesNotMatch(await page.locator("#dtModelBoard").innerText(), /2330/);
  await page.locator(".language-menu summary").click();
  await page.locator('[data-lang="en"]').click();
  assert.match(await page.locator("#dtModelCadence").innerText(), /TW.*Paused: closed session/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(errors, []);
  await page.close();
});

test("slow automatic scans coalesce and empty unknown markets are not mistaken for closed", async () => {
  const { page, errors, calls } = await pollingPage("zh-tw", false);
  let release;
  let hold = true;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/movers?**", async (route) => {
    if (hold) await held;
    const now = await page.evaluate(() => Date.now());
    await route.fulfill({ json: pollingFixture(["TW"], { TW: "unknown" }, now) });
  });
  await page.locator('[data-workspace-tab="daytrade"]').click();
  await page.waitForFunction(() => daytradeRequests.has("model"));
  await page.clock.runFor(16000);
  assert.equal(calls.filter((call) => call.path === "/api/movers").length, 1, "pending scan is not restarted at 8s or 16s");
  release(); hold = false;
  await settleDaytrade(page);
  assert.equal(await page.locator("#dtModelCadence").getAttribute("data-state"), "running");
  assert.equal(await page.locator("#dtModelBoard .model-mover").count(), 0);
  await page.clock.runFor(8000);
  await settleDaytrade(page);
  assert.equal(calls.filter((call) => call.path === "/api/movers").length, 2);
  assert.deepEqual(errors, []);
  await page.close();
});

test("a newly closed session prevents late quotes and detail responses from starting more work", async () => {
  const { page, errors, calls } = await pollingPage("zh-tw", false);
  let state = "regular", hold = false, release;
  const held = new Promise((resolve) => { release = resolve; });
  await page.route("**/api/movers?**", async (route) => {
    const now = await page.evaluate(() => Date.now());
    await route.fulfill({ json: pollingFixture(["TW"], { TW: state }, now) });
  });
  await page.route("**/api/quote/**", async (route) => {
    if (hold) await held;
    await route.fulfill({ json: { ok: true, symbol: new URL(route.request().url()).pathname.split("/").at(-1), price: 100 } });
  });
  await page.route("**/api/daytrade/analyze", async (route) => {
    if (!hold) return route.fulfill({ json: { ok: false } });
    await held;
    await route.fulfill({ json: { ok: true, latest: { price: 999 } } });
  });
  await page.locator('[data-workspace-tab="daytrade"]').click();
  await settleDaytrade(page);
  hold = true;
  await page.clock.runFor(8000);
  await page.waitForFunction(() => daytradeRequests.has("quotes"));
  await page.evaluate(() => { analyzeDaytradeSymbol("2330.TW", true); });
  await page.waitForFunction(() => daytradeRequests.has("analysis"));
  state = "closed";
  await page.clock.runFor(8000);
  await page.waitForFunction(() => document.getElementById("dtModelCadence").dataset.state === "closed");
  const closedCalls = calls.length;
  release();
  await settleDaytrade(page);
  assert.equal(calls.length, closedCalls, "late responses start neither more details nor a cost calculation");
  assert.notEqual(await page.locator("#dtBuyPrice").inputValue(), "999");
  assert.deepEqual(errors, []);
  await page.close();
});

test("intraday candidates and closed references stay distinct, accessible, and responsive", async () => {
  for (const [width, locale] of [[320, "zh-tw"], [390, "en"], [768, "zh-cn"], [843, "zh-tw"], [1440, "zh-tw"]]) {
    const { page, errors, requests } = await pageFor(width, locale);
    await page.route("**/api/movers?**", (route) => {
      requests.push("/api/movers");
      const data = moverFixture();
      for (const invalid of ["STALE", "FUTURE", "UNKNOWN", "EXPIRED", "DAILY"]) {
        const row = structuredClone(data.long_candidates[0]); row.symbol = invalid;
        if (invalid === "STALE") row.data_status.price.as_of = new Date(Date.now() - 1800000).toISOString();
        if (invalid === "FUTURE") row.data_status.price.as_of = new Date(Date.now() + 30000).toISOString();
        if (invalid === "UNKNOWN") row.data_status.market.state = "unknown";
        if (invalid === "EXPIRED") row.data_status.valid_until = new Date(Date.now() - 1000).toISOString();
        if (invalid === "DAILY") row.interval = "1d";
        data.long_candidates.push(row);
      }
      return route.fulfill({ json: data });
    });
    await page.locator('[data-workspace-tab="daytrade"]').click();
    await page.locator('#dtModelBoard [data-model-symbol="2330.TW"]').waitFor();
    assert.equal(await page.locator("#dtModelBoard .model-mover").count(), 1);
    assert.equal(await page.locator("#dtModelReference").isVisible(), true);
    assert.equal(await page.locator("#dtReferenceBoard").isVisible(), false);
    await page.locator("#dtModelReference > summary").click();
    assert.match(await page.locator("#dtReferenceBoard").innerText(), /LAST.TW/);
    assert.match(await page.locator("#dtReferenceBoard .model-time").innerText(), /1d/);
    assert.match(await page.locator("#dtReferenceBoard").innerText(), locale === "en" ? /Daily fallback/ : locale === "zh-cn" ? /日线备用/ : /日線備援/);
    assert.doesNotMatch(await page.locator("#dtModelBoard").innerText(), /LAST|STALE|FUTURE|UNKNOWN|EXPIRED|DAILY|UNSAFE/);
    assert.match(await page.locator("#dtModelBoard .model-time").innerText(), /5m/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `model overflow at ${width}/${locale}`);
    assert.equal(await page.locator(".model-mover").evaluateAll((buttons) => buttons.some((button) => button.scrollWidth > button.clientWidth)), false, `row overflow at ${width}/${locale}`);
    assert.ok(await page.locator("#dtModelBoard .model-time").evaluate((el) => el.clientWidth) >= 240, `timestamp width at ${width}/${locale}`);
    if (process.env.UI_SCREENSHOTS) {
      fs.mkdirSync(process.env.UI_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.UI_SCREENSHOTS, `intraday-${width}-${locale}.png`), fullPage: true });
    }
    await page.locator('#dtModelBoard [data-model-symbol="2330.TW"]').focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.querySelector(".wrap").dataset.view === "overview");
    assert.equal(requests.filter((p) => p === "/api/movers").length, 1);
    assert.deepEqual(errors, []);
    await page.close();
  }
});

test("model expiration, market races, failures, and missing contracts never retain unsafe candidates", async () => {
  const { page, errors, requests } = await pageFor(390);
  await page.clock.install({ time: new Date() });
  await page.reload();
  await page.waitForFunction(() => document.getElementById("price").textContent === "100");
  let mode = "expiry";
  let calls = 0;
  await page.route("**/api/movers?**", async (route) => {
    calls++;
    const market = new URL(route.request().url()).searchParams.get("markets");
    if (mode === "failure") return route.fulfill({ status: 503, json: { ok: false } });
    if (mode === "legacy") return route.fulfill({ json: { ok: true, markets: [market], gainers: [{ symbol: "UNSAFE" }], losers: [] } });
    const data = moverFixture(market);
    if (mode === "expiry") data.long_candidates[0].data_status.valid_until = new Date(Date.now() + 5000).toISOString();
    if (mode === "race" && market === "TW") await pause(700);
    await route.fulfill({ json: data });
  });
  await page.locator('[data-workspace-tab="daytrade"]').click();
  await page.locator('#dtModelBoard [data-model-symbol="2330.TW"]').waitFor();
  const before = calls;
  await page.clock.runFor(31000);
  assert.equal(await page.locator("#dtModelBoard .model-mover").count(), 0);
  assert.match(await page.locator("#dtModelNotice").innerText(), /過期/);
  assert.equal(calls, before, "local expiration sends no request");
  mode = "race";
  await page.locator("#dtRefreshBtn").click();
  await page.waitForFunction(() => document.getElementById("dtModelMeta").textContent.includes("載入中"));
  await page.locator("#dtMarketMode").selectOption("US");
  await page.locator('#dtModelBoard [data-model-symbol="MU"]').waitFor();
  await pause(850);
  assert.doesNotMatch(await page.locator("#dtModelBoard").innerText(), /2330/);
  mode = "failure";
  await page.locator("#dtRefreshBtn").click();
  await page.waitForFunction(() => document.getElementById("dtModelMeta").textContent.includes("失敗"));
  assert.equal(await page.locator("#dtModelBoard .model-mover").count(), 0);
  assert.equal(await page.locator("#dtModelReference").isVisible(), false);
  mode = "legacy";
  await page.locator("#dtRefreshBtn").click();
  await page.waitForFunction(() => document.getElementById("dtModelMeta").textContent.includes("盤中候選 0"));
  assert.doesNotMatch(await page.locator("#dtModelBoard").innerText(), /UNSAFE/);
  assert.deepEqual(errors, []);
  await page.close();
});

test("valuation provenance and opt-in diagnostics stay separate from chart data", async () => {
  const { page, errors, requests } = await pageFor(390);
  await page.waitForFunction(() => document.getElementById("valuationMeta").textContent.includes("來源報告值"));
  assert.match(await page.locator("#priceMeta").innerText(), /Yahoo chart/);
  assert.match(await page.locator("#valuationMeta").innerText(), /未提供資料時點/);
  assert.doesNotMatch(await page.locator("#valuationMain").innerText(), /88%/);
  assert.equal(requests.includes("/api/diagnostics"), false);
  await page.locator("#diagnosticsPanel > summary").click();
  await page.locator("#metricsBody tr").waitFor();
  assert.equal(requests.filter((p) => p === "/api/diagnostics").length, 1);
  assert.match(await page.locator("#metricsBody").innerText(), /4.0 KB/);
  await page.locator("#refreshDiagnostics").click();
  await page.waitForFunction(() => !document.getElementById("refreshDiagnostics").disabled);
  assert.equal(requests.filter((p) => p === "/api/diagnostics").length, 2);
  await page.locator("#symbol").fill("SLOWVAL"); await page.locator("#runBtn").click();
  await page.waitForFunction(() => document.getElementById("ticker").textContent === "SLOWVAL" && document.getElementById("queryStatus").hidden);
  await page.locator("#symbol").fill("MU"); await page.locator("#runBtn").click();
  await page.waitForFunction(() => lastData?.symbol === "MU" && lastData?.valuation?.market_cap === 999000000);
  const cap = await page.locator("#valuationMain").innerText();
  await pause(800);
  assert.equal(await page.locator("#valuationMain").innerText(), cap);
  await page.locator("#symbol").fill("NOVAL"); await page.locator("#runBtn").click();
  await page.waitForFunction(() => document.getElementById("valuationMeta").textContent === "無可核實估值");
  assert.equal(await page.locator("#valuationMain").innerText(), "-");
  assert.match(await page.locator("#priceMeta").innerText(), /USD/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(errors, []);
  await page.close();
});

test("reported and estimated valuation fields have independent labels and dates", async () => {
  const { page, errors } = await pageFor(320);
  await page.locator("#symbol").fill("ESTIMATE"); await page.locator("#runBtn").click();
  await page.waitForFunction(() => document.getElementById("valuationMeta").textContent.includes("計算估值"));
  assert.match(await page.locator("#valuationMain").innerText(), /估算/);
  assert.doesNotMatch(await page.locator("#valuationSub").innerText(), /估算/);
  assert.match(await page.locator("#dataProvenance").textContent(), /已發行股數快照/);
  await page.locator("#symbol").fill("MIXED"); await page.locator("#runBtn").click();
  await page.waitForFunction(() => document.getElementById("valuationMeta").textContent.includes("Nasdaq"));
  assert.doesNotMatch(await page.locator("#valuationMain").innerText(), /估算/);
  assert.match(await page.locator("#valuationSub").innerText(), /估算/);
  assert.match(await page.locator("#valuationMeta").innerText(), /來源報告值.*未提供資料時點/);
  assert.match(await page.locator("#dataProvenance").textContent(), /近四季 EPS 截止日.*EPS 4/);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(errors, []);
  await page.close();
});

test("session and freshness warnings distinguish closed, delayed, unknown and fallback data", async () => {
  const { page, errors, requests } = await pageFor(320);
  for (const [symbol, expected] of [["SESSIONLIVE", "常規盤中"], ["SESSIONCLOSED", "休市：常規週末"], ["SESSIONUNKNOWN", "市場狀態未確認"], ["SESSIONEXPIRED", "市場狀態待刷新"], ["SESSIONFALLBACK", "常規盤中"], ["SESSIONFUTURE", "常規盤中"], ["SESSIONPOST", "盤後"]]) {
    await page.locator("#symbol").fill(symbol); await page.locator("#runBtn").click();
    await page.waitForFunction((target) => document.getElementById("ticker").textContent === target && document.getElementById("queryStatus").hidden, symbol);
    assert.match(await page.locator("#marketSessionState").innerText(), new RegExp(expected));
    if (symbol === "SESSIONLIVE") assert.match(await page.locator("#marketDataWarning").innerText(), /盤中資料落後/);
    if (symbol === "SESSIONCLOSED") {
      assert.match(await page.locator("#priceQualityState").innerText(), /最後交易時段/);
      assert.equal(await page.locator("#marketDataWarning").isVisible(), false);
    }
    if (symbol === "SESSIONFALLBACK") assert.match(await page.locator("#marketDataWarning").innerText(), /日線備援，不適用日內/);
    if (symbol === "SESSIONFUTURE") assert.match(await page.locator("#marketDataWarning").innerText(), /時間在未來/);
    if (symbol === "SESSIONPOST") assert.match(await page.locator("#priceQualityState").innerText(), /僅常規盤價格/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  }
  assert.match(await page.locator("#refreshMeta").innerText(), /畫面載入/);
  assert.equal(requests.some((url) => /session|calendar|freshness/.test(url)), false);
  assert.deepEqual(errors, []);
  await page.close();
});

test("intraday and swing presets persist layers, intervals, and expanded sections", async () => {
  const { page, errors } = await pageFor(390);
  await page.locator("#workspacePreset").selectOption("daytrade");
  await page.waitForFunction(() => document.getElementById("queryStatus").hidden);
  assert.equal(await page.locator("#period").inputValue(), "1d");
  assert.equal(await page.locator("#interval").inputValue(), "5m");
  await page.locator(".indicator-menu summary").click();
  await page.locator('.chart-switch:has([data-chart-layer="bias25"])').click();
  await page.locator(".indicator-menu summary").click();
  assert.match(await page.locator("#bias25Panel strong").innerText(), /25 根.*5m/);
  await page.locator('[data-ui="modelDetails"]').click();
  await page.locator("#savePreset").click();
  await page.locator("#workspacePreset").selectOption("swing");
  await page.waitForFunction(() => document.getElementById("queryStatus").hidden);
  assert.equal(await page.locator("#interval").inputValue(), "1d");
  assert.match(await page.locator("#bias25Panel strong").innerText(), /25 日/);
  await page.locator("#workspacePreset").selectOption("daytrade");
  await page.waitForFunction(() => document.getElementById("queryStatus").hidden);
  assert.equal(await page.locator('[data-ui="modelDetails"]').evaluate((el) => el.parentElement.open), true);
  await page.reload();
  await page.waitForFunction(() => document.getElementById("queryStatus").hidden);
  assert.equal(await page.locator("#workspacePreset").inputValue(), "daytrade");
  assert.equal(await page.locator("#interval").inputValue(), "5m");
  assert.match(await page.locator("#bias25Panel strong").innerText(), /25 根/);
  await page.locator("#enrichAnalysis").click();
  await page.waitForFunction(() => document.getElementById("analysisScope").textContent === "技術、消息與事件模型");
  await page.locator("#resetPreset").click();
  await page.waitForFunction(() => document.getElementById("queryStatus").hidden);
  assert.equal(await page.locator("#bias25Panel strong").count(), 0);
  assert.deepEqual(errors, []);
  await page.close();
});

test("query replaces stale values, ignores old responses, and recovers from errors", async () => {
  const { page, errors } = await pageFor();
  async function query(symbol) { await page.locator("#symbol").fill(symbol); await page.locator("#runBtn").click(); }
  await query("SLOW");
  assert.equal(await page.locator("#price").innerText(), "-");
  assert.equal(await page.locator("#chartState").isVisible(), true);
  assert.match(await page.locator("#chart").getAttribute("aria-label"), /^SLOW /);
  await query("MU");
  await page.waitForFunction(() => document.getElementById("price").textContent === "150");
  await pause(900);
  assert.equal(await page.locator("#ticker").innerText(), "MU");
  assert.equal(await page.locator("#price").innerText(), "150");
  await query("FAIL");
  await page.locator("#queryRetry").waitFor({ state: "visible" });
  assert.equal(await page.locator("#price").innerText(), "-");
  await query("WRONG");
  await page.locator("#queryRetry").waitFor({ state: "visible" });
  assert.equal(await page.locator("#price").innerText(), "-");
  await query("EMPTY");
  await page.waitForFunction(() => document.getElementById("queryStatus").hidden);
  assert.match(await page.locator("#chartState").innerText(), /沒有 K 線/);
  await query("MU");
  await page.waitForFunction(() => document.getElementById("price").textContent === "150");
  assert.equal(await page.locator("#chartState").isVisible(), false);
  assert.deepEqual(errors, []);
  await page.close();
});

test("language, menus, notifications, and watchlist work on a narrow screen", async () => {
  const { page, errors } = await pageFor(390, "en");
  assert.equal(await page.locator('[data-workspace-tab="overview"]').innerText(), "Overview");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.locator(".language-menu summary").click();
  await page.locator('[data-lang="zh-cn"]').click();
  assert.equal(await page.locator('[data-workspace-tab="overview"]').innerText(), "个股总览");
  await page.locator("#alertBellBtn").click();
  assert.equal(await page.locator("#alertBellBtn").getAttribute("aria-expanded"), "true");
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("#alertBellBtn").getAttribute("aria-expanded"), "false");
  await page.locator("#addWatchBtn").click();
  assert.ok(await page.evaluate(() => JSON.parse(localStorage.getItem("srr.watch")).includes("2330.TW")));
  await page.locator(".nav-more summary").click();
  await page.locator('[data-workspace-tab="portfolio"]').click();
  assert.equal(await page.locator("#pane-portfolio").isVisible(), true);
  assert.equal(await page.locator(".nav-more summary").evaluate((el) => el.parentElement.classList.contains("has-active")), true);
  await page.keyboard.press("Control+k");
  assert.equal(await page.locator("#symbol").evaluate((el) => el === document.activeElement), true);
  assert.equal(await page.locator("#chart").isVisible(), true);
  assert.deepEqual(errors, []);
  await page.close();
});
