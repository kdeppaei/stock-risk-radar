/* Data provenance, opt-in diagnostics, and local trading presets. */
window.OpenKiriWorkflow = (() => {
  const $ = (id) => document.getElementById(id);
  const words = {
    en: { preset: "Workspace preset", custom: "Custom", daytrade: "Intraday", swing: "Swing", savePreset: "Save preset", resetPreset: "Reset preset", saved: "Saved on this device", reset: "Default restored", storageError: "Cannot save on this device", enrich: "Load news and event analysis", diagnostics: "Traffic and performance", refreshMetrics: "Refresh metrics", metricsNote: "Since process start, not Render billing. Resets on deployment.", metricsUnits: "Sent: compressed API bodies / estimated upstream HTTP requests. Received: decoded upstream bodies. P95: last 128 calls. Excludes TLS and platform traffic.", category: "Category", requests: "Requests", cacheHits: "Cache hits", latency: "Average / P95", sent: "Sent", received: "Received", errors: "Errors", unavailable: "Unavailable", unknownTime: "Source time not provided", retrieved: "Retrieved", sourceTime: "Source time", price: "Price", chart: "Chart (bar start)", valuation: "Market cap / P/E", reported: "Provider reported", technical: "Technical model only; news and events not loaded", full: "Technical, news and event model", delayed: "Not verified real-time", loading: "Loading", failed: "Request failed; retry", missingValuation: "No verified valuation", valuationRule: "Market cap changes are not money flow", barBias: "25-bar bias", dayBias: "25-day bias", weekBias: "25-week bias" },
    "zh-tw": { preset: "操作預設", custom: "自訂", daytrade: "當沖", swing: "波段", savePreset: "儲存預設", resetPreset: "還原預設", saved: "已儲存於此裝置", reset: "已還原預設", storageError: "此裝置無法儲存", enrich: "載入消息與事件分析", diagnostics: "流量與效能診斷", refreshMetrics: "刷新統計", metricsNote: "本程序啟動後統計，非 Render 帳單；重新部署會歸零。", metricsUnits: "送出：API 壓縮後本文／上游 HTTP 請求估計。接收：上游解壓後本文。P95：最近 128 次。未含 TLS 與平台流量。", category: "分類", requests: "請求", cacheHits: "快取命中", latency: "平均 / P95", sent: "送出", received: "接收", errors: "失敗", unavailable: "暫無資料", unknownTime: "來源未提供資料時點", retrieved: "取得時間", sourceTime: "資料時點", price: "報價", chart: "K 線（起始時點）", valuation: "市值 / P/E", reported: "來源報告值", technical: "僅技術模型；尚未載入消息與事件", full: "技術、消息與事件模型", delayed: "非逐筆即時保證", loading: "載入中", failed: "請求失敗，請重試", missingValuation: "無可核實估值", valuationRule: "市值變動不等於資金流入流出", barBias: "25 根 K 線乖離率", dayBias: "25 日乖離率", weekBias: "25 週乖離率" }
  };
  words["zh-cn"] = { preset: "操作预设", custom: "自定义", daytrade: "日内", swing: "波段", savePreset: "保存预设", resetPreset: "还原预设", saved: "已保存于此设备", reset: "已还原预设", storageError: "此设备无法保存", enrich: "加载消息与事件分析", diagnostics: "流量与性能诊断", refreshMetrics: "刷新统计", metricsNote: "本进程启动后统计，非 Render 账单；重新部署会归零。", metricsUnits: "发送：API 压缩后正文／上游 HTTP 请求估计。接收：上游解压后正文。P95：最近 128 次。未含 TLS 与平台流量。", category: "分类", requests: "请求", cacheHits: "缓存命中", latency: "平均 / P95", sent: "发送", received: "接收", errors: "失败", unavailable: "暂无数据", unknownTime: "来源未提供数据时间", retrieved: "获取时间", sourceTime: "数据时间", price: "报价", chart: "K 线（起始时间）", valuation: "市值 / P/E", reported: "来源报告值", technical: "仅技术模型；尚未加载消息与事件", full: "技术、消息与事件模型", delayed: "非逐笔实时保证", loading: "加载中", failed: "请求失败，请重试", missingValuation: "无可核实估值", valuationRule: "市值变动不等于资金流入流出", barBias: "25 根 K 线乖离率", dayBias: "25 日乖离率", weekBias: "25 周乖离率" };
  const tr = (key) => (words[lang] || words["zh-tw"])[key];
  const localTime = (value) => {
    if (!value || !Number.isFinite(Date.parse(value))) return tr("unknownTime");
    return new Date(value).toLocaleString(lang === "en" ? "en-US" : "zh-TW", { timeZoneName: "short" });
  };
  let valuationController = null;
  let valuationSequence = 0;
  let metricsPending = false;
  const valuationCache = new Map();

  function translate() {
    document.querySelectorAll("[data-workflow]").forEach((el) => { el.textContent = tr(el.dataset.workflow); });
    if (lastData) renderProvenance(lastData);
  }

  function renderProvenance(data) {
    if (!data) return;
    const price = data.quote?.provenance?.price || data.provenance?.price || {};
    const chart = data.provenance?.chart || {};
    const v = data.valuation || {};
    const currency = data.quote?.currency || v.currency || "";
    $("priceMeta").textContent = `${price.time_basis === "bar_start" ? tr("chart") : price.source || tr("unavailable")} ${currency} · ${localTime(price.as_of)}`;
    $("priceMeta").title = tr("delayed");
    $("valuationMeta").textContent = v.status === "reported" ? `${v.source} · ${tr("reported")} · ${localTime(v.as_of)}` : tr("missingValuation");
    $("valuationMeta").title = `${tr("retrieved")}: ${localTime(v.fetched_at)}. ${tr("valuationRule")}`;
    $("analysisScope").textContent = tr(data.analysis_scope === "technical_news_events" ? "full" : "technical");
    $("predictionBadge").title = $("analysisScope").textContent;
    $("enrichAnalysis").disabled = data.analysis_scope === "technical_news_events";
    const mount = $("dataProvenance");
    mount.replaceChildren();
    [["price", price], ["chart", chart], ["valuation", v]].forEach(([name, meta]) => {
      const section = document.createElement("div");
      const heading = document.createElement("strong");
      heading.textContent = tr(name);
      section.append(heading);
      [meta.source || tr("unavailable"), `${tr("sourceTime")}: ${localTime(meta.as_of)}`, `${tr("retrieved")}: ${localTime(meta.fetched_at)}`, tr(name === "valuation" ? "valuationRule" : "delayed")].forEach((text) => {
        const row = document.createElement("span"); row.textContent = text; section.append(row);
      });
      mount.append(section);
    });
  }

  function cancelValuation() {
    valuationSequence++;
    valuationController?.abort();
  }

  async function loadValuation(symbol) {
    cancelValuation();
    const sequence = valuationSequence;
    const controller = new AbortController();
    valuationController = controller;
    const timer = setTimeout(() => controller.abort(), 15000);
    $("valuationMeta").textContent = tr("loading");
    try {
      const cached = valuationCache.get(symbol);
      let data;
      if (cached && Date.now() - cached.time < 300000) data = cached.data;
      else {
        const response = await fetch(`/api/valuation?symbol=${encodeURIComponent(symbol)}`, { signal: controller.signal });
        data = await response.json();
        if (!response.ok || !data.ok || !data.valuation || !sameSymbol(symbol, data.symbol) || !sameSymbol(symbol, data.valuation.symbol)) throw new Error("Invalid valuation");
        valuationCache.set(symbol, { time: Date.now(), data });
        if (valuationCache.size > 40) valuationCache.delete(valuationCache.keys().next().value);
      }
      if (sequence !== valuationSequence || !lastData || !sameSymbol(lastData.symbol, symbol)) return;
      lastData.valuation = data.valuation;
      $("valuationMain").textContent = valuationCapText(lastData);
      $("valuationSub").textContent = peText(lastData);
      renderProvenance(lastData);
    } catch {
      if (sequence === valuationSequence && lastData && sameSymbol(lastData.symbol, symbol)) renderProvenance(lastData);
    } finally { clearTimeout(timer); }
  }

  async function loadMetrics() {
    if (metricsPending) return;
    metricsPending = true;
    $("refreshDiagnostics").disabled = true;
    $("metricsStatus").textContent = tr("loading");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch("/api/diagnostics", { signal: controller.signal });
      const data = await response.json();
      if (!response.ok || !data.ok || !Array.isArray(data.rows)) throw new Error("Metrics unavailable");
      $("metricsStatus").textContent = `${localTime(data.since)} — ${localTime(new Date().toISOString())}`;
      $("metricsBody").replaceChildren();
      const bytes = (n) => `${(Number(n || 0) / 1024).toFixed(1)} KB`;
      data.rows.forEach((row) => {
        const tr = document.createElement("tr");
        [row.name, row.requests, row.cache_hit_pct == null ? "-" : `${row.cache_hit_pct}%`, row.name.startsWith("Cache ") ? "-" : `${row.avg_ms} / ${row.p95_ms} ms`, bytes(row.sent_bytes), bytes(row.received_bytes), row.errors].forEach((value) => {
          const td = document.createElement("td"); td.textContent = value; tr.append(td);
        });
        $("metricsBody").append(tr);
      });
    } catch { $("metricsStatus").textContent = tr("failed"); }
    finally { clearTimeout(timer); metricsPending = false; $("refreshDiagnostics").disabled = false; }
  }

  function readPresets() {
    try {
      const stored = JSON.parse(localStorage.getItem("okiri.presets.v1") || "{}");
      return stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
    } catch { return {}; }
  }
  const presetNames = ["custom", "daytrade", "swing"];
  const detailSections = () => [...document.querySelectorAll(".overview-details")].filter((el) => el.querySelector("summary[data-ui]"));
  function defaults(name) {
    return { period: name === "daytrade" ? "1d" : name === "swing" ? "6mo" : "1y", interval: name === "daytrade" ? "5m" : "1d", layers: { ...DEFAULT_CHART_LAYERS, ma60: name !== "daytrade", bias25: name === "swing" }, expanded: [] };
  }
  function applyPreset(name, run = true, reset = false) {
    if (!presetNames.includes(name)) return;
    const stored = readPresets();
    const settings = reset ? defaults(name) : stored[name] || defaults(name);
    const period = ["1d", "6mo", "1y", "2y", "5y"].includes(settings.period) ? settings.period : defaults(name).period;
    const interval = ["5m", "1d", "1wk"].includes(settings.interval) ? settings.interval : defaults(name).interval;
    $("period").value = period; $("interval").value = interval;
    chartLayers = { ...DEFAULT_CHART_LAYERS };
    for (const key of Object.keys(chartLayers)) if (typeof settings.layers?.[key] === "boolean") chartLayers[key] = settings.layers[key];
    detailSections().forEach((el) => { el.open = Array.isArray(settings.expanded) && settings.expanded.includes(el.querySelector("summary").dataset.ui); });
    if (["TW", "US", "US,TW"].includes(settings.market)) {
      daytradeMarketMode = settings.market; $("dtMarketMode").value = settings.market;
    }
    $("workspacePreset").value = name;
    $("dashboard").dataset.preset = name;
    try { localStorage.setItem("okiri.activePreset", name); localStorage.setItem("okiri.chartLayers", JSON.stringify(chartLayers)); } catch {}
    renderChartLayerControls(); renderBias25Panel(lastData); renderChart(lastData);
    $("presetStatus").textContent = "";
    if (run) analyze($("symbol").value);
  }
  function savePreset() {
    const name = $("workspacePreset").value;
    const all = readPresets();
    all[name] = { period: $("period").value, interval: $("interval").value, layers: { ...chartLayers }, market: daytradeMarketMode,
      expanded: detailSections().filter((el) => el.open).map((el) => el.querySelector("summary").dataset.ui) };
    try {
      localStorage.setItem("okiri.presets.v1", JSON.stringify(all));
      localStorage.setItem("okiri.activePreset", name);
      $("presetStatus").textContent = tr("saved");
    } catch { $("presetStatus").textContent = tr("storageError"); }
  }
  function biasLabel(interval) {
    return tr(interval === "1d" || !interval ? "dayBias" : interval === "1wk" ? "weekBias" : "barBias") + (["5m", "15m", "1h"].includes(interval) ? ` (${interval})` : "");
  }
  function boot() {
    translate();
    let active;
    try { active = localStorage.getItem("okiri.activePreset"); } catch {}
    if (presetNames.includes(active)) applyPreset(active, false);
    $("workspacePreset").addEventListener("change", () => applyPreset($("workspacePreset").value));
    $("savePreset").addEventListener("click", savePreset);
    $("resetPreset").addEventListener("click", () => {
      const name = $("workspacePreset").value;
      const all = readPresets(); delete all[name];
      try { localStorage.setItem("okiri.presets.v1", JSON.stringify(all)); } catch {}
      applyPreset(name, true, true); $("presetStatus").textContent = tr("reset");
    });
    $("diagnosticsPanel").addEventListener("toggle", () => { if ($("diagnosticsPanel").open) loadMetrics(); });
    $("refreshDiagnostics").addEventListener("click", loadMetrics);
    $("enrichAnalysis").addEventListener("click", () => { if (lastData) analyze(lastData.symbol, false, true); });
  }
  return { boot, translate, renderProvenance, loadValuation, cancelValuation, biasLabel };
})();
