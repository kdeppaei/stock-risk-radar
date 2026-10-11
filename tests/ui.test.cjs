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
    } else if (url.pathname === "/api/diagnostics") data = { ok: true, since: "2026-03-05T00:00:00Z", rows: [{ name: "API /api/analyze", requests: 4, cache_hit_pct: 50, avg_ms: 20, p95_ms: 30, sent_bytes: 4096, received_bytes: 0, errors: 0 }] };
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
