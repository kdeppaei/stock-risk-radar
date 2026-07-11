(() => {
  const $ = (id) => document.getElementById(id);
  const tw = document.documentElement.lang !== "en";
  const text = {
    market: tw ? "市場" : "Market",
    both: tw ? "台美" : "TW + US",
    tw: tw ? "台股" : "Taiwan",
    us: tw ? "美股" : "US",
    cap: tw ? "市值" : "Mkt cap",
    long: tw ? "偏做多" : "Long bias",
    short: tw ? "偏做空" : "Short bias",
    model: tw ? "T+0 模型多空分類" : "T+0 long/short model",
    alerts: tw ? "當日重大訊號" : "Major signals",
    empty: tw ? "目前沒有重大交叉訊號" : "No major cross signal right now",
    loading: tw ? "載入中" : "Loading",
    scanned: tw ? "掃描" : "Scanned",
  };
  Object.assign(text, {
    market: tw ? "\u5e02\u5834" : "Market",
    both: tw ? "\u53f0\u7f8e" : "TW + US",
    tw: tw ? "\u53f0\u80a1" : "Taiwan",
    us: tw ? "\u7f8e\u80a1" : "US",
    cap: tw ? "\u5e02\u503c" : "Mkt cap",
    long: tw ? "\u504f\u591a" : "Long bias",
    short: tw ? "\u504f\u7a7a" : "Short bias",
    model: tw ? "T+0 \u591a\u7a7a\u6a21\u578b" : "T+0 long/short model",
    alerts: tw ? "\u91cd\u5927\u8a0a\u865f" : "Major signals",
    empty: tw ? "\u76ee\u524d\u6c92\u6709\u91cd\u5927\u4ea4\u53c9\u8a0a\u865f" : "No major cross signal right now",
    loading: tw ? "\u8f09\u5165\u4e2d" : "Loading",
    scanned: tw ? "\u6383\u63cf" : "Scanned",
  });
  const defaults = ["2330.TW", "2317.TW", "2454.TW", "3481.TW", "2409.TW", "2002.TW", "1101.TW", "2303.TW", "2881.TW", "0050.TW", "006208.TW"];
  let marketMode = localStorage.getItem("okiri.daytradeMarket") || "TW";
  let moverTimer = null;

  function fmt(n) {
    if (n == null || Number.isNaN(Number(n))) return "-";
    return Number(n).toLocaleString(tw ? "zh-TW" : "en-US", { maximumFractionDigits: 2 });
  }
  function signed(n, suffix = "") {
    const value = Number(n || 0);
    return `${value >= 0 ? "+" : ""}${fmt(value)}${suffix}`;
  }
  function arrow(n) { return Number(n || 0) >= 0 ? "▲" : "▼"; }
  arrow = (n) => Number(n || 0) >= 0 ? "\u25b2" : "\u25bc";
  function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[m])); }
  function compact(n) { return Number(n || 0).toLocaleString(tw ? "zh-TW" : "en-US", { notation: "compact", maximumFractionDigits: 1 }); }
  function money(n, currency = "USD") {
    const code = String(currency || "USD").toUpperCase();
    return `${code === "USD" ? "$" : code === "TWD" ? "NT$" : `${code} `}${compact(n)}`;
  }
  function markets() { return new Set(String(marketMode).split(",").filter(Boolean)); }
  function symbolMarket(symbol) {
    const value = String(symbol || "").toUpperCase();
    return value.endsWith(".TW") || value.endsWith(".TWO") || /^\d{4,6}$/.test(value) ? "TW" : "US";
  }
  function normalizeSymbol(symbol) {
    const value = String(symbol || "").trim().toUpperCase();
    if (/^\d{4,6}$/.test(value)) return `${value}.TW`;
    return value;
  }

  function installStyle() {
    if ($("okiriLiveStyle")) return;
    const style = document.createElement("style");
    style.id = "okiriLiveStyle";
    style.textContent = `
      .okiri-alert-shell{position:relative;display:inline-flex}
      .okiri-alert-bell{position:relative;width:42px;padding:0}
      .okiri-alert-count{position:absolute;right:-5px;top:-6px;min-width:18px;height:18px;border-radius:999px;background:#ef4444;color:#fff;font-size:11px;display:none;place-items:center}
      .okiri-alert-drawer{display:none;position:absolute;right:0;top:48px;z-index:120;width:min(390px,92vw);max-height:470px;overflow:auto;background:#101a35;border:1px solid var(--line);border-radius:8px;box-shadow:0 24px 70px rgba(0,0,0,.45);padding:10px}
      .okiri-alert-drawer.open{display:grid;gap:8px}
      .okiri-alert-item{display:grid;gap:5px;background:var(--card);border:1px solid var(--line);border-radius:7px;padding:9px;cursor:pointer}
      .okiri-alert-item.good{box-shadow:inset 4px 0 0 #22c55e}.okiri-alert-item.bad{box-shadow:inset 4px 0 0 #ef4444}
      .okiri-alert-item small{color:var(--muted)}
      .okiri-market-select{min-width:96px}.okiri-model-panel{margin-top:0}
      .okiri-cap small{display:block;margin-top:2px}.okiri-up{color:#22c55e}.okiri-down{color:#ef4444}
      @media(max-width:760px){.okiri-alert-drawer{right:-90px}.mover-board{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);
  }

  function marketSelect(id, value) {
    return `<select id="${id}" class="okiri-market-select">
      <option value="TW" ${value === "TW" ? "selected" : ""}>${text.tw}</option>
      <option value="US" ${value === "US" ? "selected" : ""}>${text.us}</option>
      <option value="US,TW" ${value === "US,TW" ? "selected" : ""}>${text.both}</option>
    </select>`;
  }

  function installBell() {
    if ($("okiriAlertBell")) return;
    const target = document.querySelector(".langbar");
    if (!target) return;
    const shell = document.createElement("div");
    shell.className = "okiri-alert-shell";
    shell.innerHTML = `<button id="okiriAlertBell" class="secondary okiri-alert-bell" type="button" title="${text.alerts}">🔔<span id="okiriAlertCount" class="okiri-alert-count">0</span></button><div id="okiriAlertDrawer" class="okiri-alert-drawer"></div>`;
    target.prepend(shell);
    $("okiriAlertBell").addEventListener("click", () => {
      $("okiriAlertDrawer").classList.toggle("open");
      if ($("okiriAlertDrawer").classList.contains("open")) loadAlerts();
    });
  }

  function installDaytradeSwitch() {
    if ($("okiriDtMarket")) return;
    const dtRefresh = $("dtRefreshBtn");
    const actions = dtRefresh?.parentElement;
    if (!actions) return;
    const label = document.createElement("label");
    label.innerHTML = `<span class="muted">${text.market}</span>${marketSelect("okiriDtMarket", marketMode)}`;
    actions.prepend(label);
    $("okiriDtMarket").addEventListener("change", () => {
      marketMode = $("okiriDtMarket").value;
      localStorage.setItem("okiri.daytradeMarket", marketMode);
      refreshDaytradeQuotesLive();
      loadDaytradeModelBoard();
      loadAlerts();
    });
  }

  function installModelPanel() {
    if ($("okiriDtModelBoard")) return;
    const firstGrid = document.querySelector("#pane-daytrade .desk-grid");
    if (!firstGrid) return;
    const panel = document.createElement("div");
    panel.className = "desk-panel okiri-model-panel";
    panel.innerHTML = `<div class="section-head"><div><h3>${text.model}</h3><p class="explain" id="okiriDtModelMeta">-</p></div><span class="desk-chip good">8s</span></div><div id="okiriDtModelBoard" class="mover-board"></div>`;
    firstGrid.insertBefore(panel, firstGrid.children[1] || null);
  }

  function installQuoteCapHeader() {
    const table = $("dtQuoteBody")?.closest("table");
    const row = table?.querySelector("thead tr");
    if (!row || row.querySelector("[data-okiri-cap]")) return;
    const th = document.createElement("th");
    th.dataset.okiriCap = "1";
    th.textContent = text.cap;
    row.insertBefore(th, row.children[2] || null);
  }

  function installMoverSwitch() {
    if ($("okiriMoverMarket")) return;
    const tools = document.querySelector(".mover-tools");
    if (!tools) return;
    const label = document.createElement("label");
    label.innerHTML = `<span class="muted">${text.market}</span>${marketSelect("okiriMoverMarket", "US,TW")}`;
    tools.prepend(label);
    $("okiriMoverMarket").addEventListener("change", () => {
      loadMoversLive();
      scheduleMoversLive();
    });
    $("moverMode")?.addEventListener("change", scheduleMoversLive);
    $("moverRefreshBtn")?.addEventListener("click", loadMoversLive);
  }

  function selectedSymbols() {
    const saved = JSON.parse(localStorage.getItem("okiri.daytradeSymbols") || "null") || defaults;
    const watch = JSON.parse(localStorage.getItem("srr.watch") || "[]");
    return [...new Set([...saved, ...watch].map(normalizeSymbol))]
      .filter((symbol) => markets().has(symbolMarket(symbol)))
      .slice(0, 32);
  }

  async function refreshDaytradeQuotesLive() {
    installQuoteCapHeader();
    const body = $("dtQuoteBody");
    if (!body) return;
    const symbols = selectedSymbols();
    if (!symbols.length) {
      body.innerHTML = `<tr><td colspan="8">-</td></tr>`;
      return;
    }
    if (!body.innerHTML.trim()) body.innerHTML = `<tr><td colspan="8">${text.loading}...</td></tr>`;
    const rows = await Promise.all(symbols.map(async (symbol) => {
      try {
        const quote = await fetch(`/api/quote/${encodeURIComponent(symbol)}`).then((res) => res.json());
        const detail = await fetch("/api/daytrade/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol: quote.symbol, buy_price: Number($("dtBuyPrice")?.value || quote.price || 0), sell_price: quote.price || 0, shares: Number($("dtShares")?.value || 1000) })
        }).then((res) => res.json()).catch(() => null);
        return { quote, detail: detail?.ok ? detail : null };
      } catch {
        return { quote: { symbol, failed: true }, detail: null };
      }
    }));
    body.innerHTML = rows.map(({ quote, detail }) => quoteRow(quote, detail)).join("");
  }

  function quoteCap(quote) {
    if (!quote.market_cap) return "-";
    const pct = quote.market_cap_change_pct ?? quote.change_pct ?? 0;
    const cls = Number(pct) >= 0 ? "okiri-up" : "okiri-down";
    return `<span>${money(quote.market_cap, quote.currency || "USD")}</span><small class="${cls}">${arrow(pct)} ${signed(pct, "%")}</small>`;
  }

  function quoteRow(quote, detail) {
    const pct = Number(quote.change_pct || 0);
    const cls = pct >= 0 ? "desk-up" : "desk-down";
    const heat = detail?.score;
    const range = detail?.metrics?.high_low_position;
    return `<tr onclick="window.analyze && analyze('${esc(quote.symbol || "")}')">
      <td><strong>${esc(quote.symbol || "-")}</strong><small class="muted">${esc(quote.source || (quote.failed ? "fallback unavailable" : ""))}</small></td>
      <td>${quote.price == null ? "-" : fmt(quote.price)}</td>
      <td class="okiri-cap">${quoteCap(quote)}</td>
      <td class="${cls}">${quote.change == null ? "-" : signed(quote.change)}</td>
      <td class="${cls}">${quote.change_pct == null ? "-" : `${arrow(pct)} ${signed(pct, "%")}`}</td>
      <td>${quote.volume_ratio == null ? "-" : fmt(quote.volume_ratio)}</td>
      <td>${range == null ? "-" : `${fmt(range)}%`}</td>
      <td><span class="desk-chip ${heat >= 70 ? "good" : heat < 45 ? "bad" : "warn"}">${heat == null ? "-" : `${heat}/100`}</span></td>
    </tr>`;
  }

  async function loadDaytradeModelBoard() {
    const board = $("okiriDtModelBoard");
    if (!board) return;
    try {
      const data = await fetch(`/api/movers?markets=${encodeURIComponent(marketMode)}&limit=6&mode=live`).then((res) => res.json());
      $("okiriDtModelMeta").textContent = `${new Date(data.updated_at || Date.now()).toLocaleTimeString()} · ${text.scanned} ${data.scanned || 0}`;
      board.innerHTML = modelColumn(text.long, data.long_candidates || data.gainers || [], "good") + modelColumn(text.short, data.short_candidates || data.losers || [], "bad");
    } catch (error) {
      $("okiriDtModelMeta").textContent = error.message;
    }
  }

  function modelColumn(title, rows, tone) {
    return `<div class="mover-column"><div class="section-head"><h2>${esc(title)}</h2><span class="tag ${tone}">${rows.length}</span></div><div class="mover-list">${rows.map((row) => {
      const cls = Number(row.change_pct || 0) >= 0 ? "up" : "down";
      return `<div class="mover-item" onclick="window.analyze && analyze('${esc(row.symbol)}')"><div><strong>${esc(row.symbol)}</strong><small class="muted">${esc(row.name || "")} · ${esc(row.pattern || "pulse")}</small></div><div class="${cls}"><strong>${arrow(row.change_pct)} ${signed(row.change_pct, "%")}</strong><small>${fmt(row.price)} · score ${esc(row.model_score ?? "-")}</small></div></div>`;
    }).join("") || `<div class="muted">-</div>`}</div></div>`;
  }

  async function loadAlerts() {
    const drawer = $("okiriAlertDrawer");
    if (!drawer) return;
    try {
      const data = await fetch(`/api/alerts?markets=${encodeURIComponent(marketMode)}&limit=10`).then((res) => res.json());
      const items = data.items || [];
      const count = $("okiriAlertCount");
      count.textContent = String(items.length);
      count.style.display = items.length ? "grid" : "none";
      drawer.innerHTML = items.length ? `<div class="section-head"><h2>${text.alerts}</h2><span class="tag">${items.length}</span></div>${items.map((item) => {
        const good = String(item.kind || "").includes("golden") || String(item.kind || "").includes("bullish");
        return `<div class="okiri-alert-item ${good ? "good" : "bad"}" onclick="document.getElementById('okiriAlertDrawer').classList.remove('open'); window.analyze && analyze('${esc(item.symbol)}')"><strong>${esc(item.symbol)} · ${esc(item.title)}</strong><small>${esc(item.name || "")} · ${arrow(item.change_pct)} ${signed(item.change_pct, "%")} · ${esc(item.interval || "")}</small><span>${esc(item.action || "")}</span></div>`;
      }).join("")}` : `<div class="muted">${text.empty}</div>`;
    } catch (error) {
      drawer.innerHTML = `<div class="muted">${esc(error.message)}</div>`;
    }
  }

  async function loadMoversLive() {
    const board = $("marketPulse");
    if (!board) return;
    const market = $("okiriMoverMarket")?.value || "US,TW";
    const mode = $("moverMode")?.value || "recent";
    try {
      const data = await fetch(`/api/movers?markets=${encodeURIComponent(market)}&limit=6&mode=${encodeURIComponent(mode)}`).then((res) => res.json());
      board.innerHTML = modelColumn(tw ? "正在漲" : "Rising", data.gainers || [], "good") + modelColumn(tw ? "正在跌" : "Falling", data.losers || [], "bad");
    } catch (error) {
      board.innerHTML = `<div class="plainbox muted">${esc(error.message)}</div>`;
    }
  }

  function scheduleMoversLive() {
    clearInterval(moverTimer);
    moverTimer = setInterval(loadMoversLive, $("moverMode")?.value === "live" ? 8000 : 60000);
  }

  function boot() {
    installStyle();
    installBell();
    installDaytradeSwitch();
    installModelPanel();
    installQuoteCapHeader();
    installMoverSwitch();
    refreshDaytradeQuotesLive();
    loadDaytradeModelBoard();
    loadAlerts();
    loadMoversLive();
    scheduleMoversLive();
    setInterval(refreshDaytradeQuotesLive, 8000);
    setInterval(loadDaytradeModelBoard, 8000);
    setInterval(loadAlerts, 60000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
