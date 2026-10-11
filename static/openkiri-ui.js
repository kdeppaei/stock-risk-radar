/* Presentation only. Data fetching remains in the existing application. */
window.OpenKiriUI = (() => {
  const $ = (id) => document.getElementById(id);
  let locale = "zh-tw";
  let pendingSymbol = "";
  let inspectedBar = -1;
  const copy = {
    "zh-tw": { skip: "跳至股票查詢", overview: "個股總覽", more: "更多", rankings: "市場排行", research: "進階研究", stock: "股票代號", period: "時間範圍", interval: "K 線週期", retry: "重試", chart: "價格走勢", indicators: "指標與訊號", sources: "外部報價", chartWaiting: "等待股票資料", signalDetails: "交叉訊號與模型判讀", technicals: "技術指標與支撐壓力", modelDetails: "模型依據與資料說明", riskDetails: "風險檢查與資料來源", loading: "正在查詢", error: "暫時無法取得資料，請重試或確認股票代號。", empty: "此期間沒有 K 線資料，請更換時間範圍。", alerts: "當日重大訊號", watch: "加入關注", ready: "資料已更新", today: "今日", sixMonths: "6 個月", year: "1 年", twoYears: "2 年", fiveYears: "5 年", fiveMinutes: "5 分鐘", day: "日線", week: "週線" },
    "zh-cn": { skip: "跳至股票查询", overview: "个股总览", more: "更多", rankings: "市场排行", research: "进阶研究", stock: "股票代码", period: "时间范围", interval: "K 线周期", retry: "重试", chart: "价格走势", indicators: "指标与信号", sources: "外部报价", chartWaiting: "等待股票数据", signalDetails: "交叉信号与模型解读", technicals: "技术指标与支撑压力", modelDetails: "模型依据与数据说明", riskDetails: "风险检查与数据来源", loading: "正在查询", error: "暂时无法取得数据，请重试或确认股票代码。", empty: "此期间没有 K 线数据，请更换时间范围。", alerts: "当日重大信号", watch: "加入关注", ready: "数据已更新", today: "今日", sixMonths: "6 个月", year: "1 年", twoYears: "2 年", fiveYears: "5 年", fiveMinutes: "5 分钟", day: "日线", week: "周线" },
    en: { skip: "Skip to stock search", overview: "Overview", more: "More", rankings: "Market rankings", research: "Research", stock: "Symbol", period: "Date range", interval: "Interval", retry: "Retry", chart: "Price chart", indicators: "Indicators", sources: "Quote sources", chartWaiting: "Waiting for stock data", signalDetails: "Cross signals and model analysis", technicals: "Technicals and price levels", modelDetails: "Model and data details", riskDetails: "Risk checks and sources", loading: "Looking up", error: "Data is unavailable. Retry or check the symbol.", empty: "No chart data for this range. Choose another range.", alerts: "Today's major signals", watch: "Add to watchlist", ready: "Data updated", today: "Today", sixMonths: "6 months", year: "1 year", twoYears: "2 years", fiveYears: "5 years", fiveMinutes: "5 minutes", day: "Daily", week: "Weekly" }
  };
  const t = (key) => (copy[locale] || copy.en)[key];

  function translate(language) {
    locale = language;
    document.querySelectorAll("[data-ui]").forEach((el) => { el.textContent = t(el.dataset.ui); });
    $("languageLabel").textContent = { "zh-tw": "繁體", "zh-cn": "简体", en: "EN" }[locale];
    ["alertBellBtn", "addWatchBtn"].forEach((id, i) => {
      $(id).title = t(i ? "watch" : "alerts");
      $(id).setAttribute("aria-label", $(id).title);
    });
    $("chart").setAttribute("aria-label", t("chart"));
    const options = { "1d": "today", "6mo": "sixMonths", "1y": "year", "2y": "twoYears", "5y": "fiveYears" };
    [...$("period").options].forEach((o) => { o.textContent = t(options[o.value]); });
    [...$("interval").options].forEach((o) => { o.textContent = t({ "5m": "fiveMinutes", "1d": "day", "1wk": "week" }[o.value]); });
    document.querySelectorAll("[data-lang]").forEach((el) => el.setAttribute("aria-pressed", String(el.dataset.lang === locale)));
    renderDaytradePollingState();
    window.OpenKiriWorkflow?.translate();
  }

  function queryState(state, symbol, hasChart = true) {
    pendingSymbol = symbol;
    $("chartTooltip").hidden = true;
    $("chartCrosshair").hidden = true;
    const loading = state === "loading";
    const failed = state === "error";
    const status = $("queryStatus");
    status.hidden = state === "ready";
    status.dataset.state = state;
    $("queryStatusText").textContent = `${symbol} · ${t(loading ? "loading" : failed ? "error" : "ready")}`;
    $("queryRetry").hidden = !failed;
    const chartState = $("chartState");
    chartState.hidden = state === "ready" && hasChart;
    chartState.dataset.state = state;
    chartState.textContent = `${symbol} · ${t(loading ? "loading" : failed ? "error" : "empty")}`;
    document.querySelector(".cards").setAttribute("aria-busy", String(loading));
    $("chart").setAttribute("aria-busy", String(loading));
    $("chart").setAttribute("aria-label", `${symbol} ${t("chart")}`);
  }

  function closeMenus(except) {
    document.querySelectorAll("details.menu[open]").forEach((menu) => { if (menu !== except) menu.open = false; });
  }

  function syncView(name) {
    $("dashboard").dataset.view = name;
    document.querySelectorAll(".workspace-pane").forEach((pane) => pane.classList.toggle("active", pane.id === `pane-${name}`));
    document.querySelectorAll("[data-workspace-tab]").forEach((button) => {
      if (button.dataset.workspaceTab === name) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    document.querySelector(".nav-more").classList.toggle("has-active", Boolean(document.querySelector(".nav-more .workspace-tab.active")));
    closeMenus();
    window.scrollTo({ top: 0, behavior: "instant" });
    if (name === "overview") requestAnimationFrame(() => renderChart(lastData));
  }

  function boot() {
    translate(lang);
    $("autoBtn").setAttribute("aria-checked", String(autoRefresh));
    $("autoBtn").addEventListener("click", () => $("autoBtn").setAttribute("aria-checked", String(autoRefresh)));
    $("queryRetry").addEventListener("click", () => analyze(pendingSymbol));
    document.querySelector("[data-home]").addEventListener("click", (event) => { event.preventDefault(); switchWorkspaceTab("overview"); });
    document.querySelector(".skip-link").addEventListener("click", (event) => { event.preventDefault(); switchWorkspaceTab("overview"); $("symbol").focus(); });
    document.addEventListener("click", (event) => {
      closeMenus(event.target.closest("details.menu"));
      if (event.target.closest("[data-lang]")) closeMenus();
      if (!event.target.closest(".alert-shell")) toggleAlertDrawer(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      const menu = event.target.closest("details.menu[open]");
      closeMenus();
      if (menu) menu.querySelector("summary").focus();
      if ($("alertDrawer").classList.contains("open")) {
        toggleAlertDrawer(false);
        $("alertBellBtn").focus();
      }
    });
    window.addEventListener("okiri:workspace", (event) => syncView(event.detail.name));
    const chart = $("chart");
    chart.tabIndex = 0;
    chart.setAttribute("aria-describedby", "chartTooltip");
    chart.addEventListener("pointermove", (event) => {
      if (!lastData?.chart?.length || !$("chartState").hidden) return;
      const rect = chart.getBoundingClientRect();
      const index = Math.round((event.clientX - rect.left - 52) / (rect.width - 122) * (lastData.chart.length - 1));
      showBar(index);
    });
    chart.addEventListener("pointerleave", hideBar);
    chart.addEventListener("blur", hideBar);
    chart.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key) || !lastData?.chart?.length) return;
      event.preventDefault();
      if (event.key === "Escape") return hideBar();
      const end = lastData.chart.length - 1;
      showBar(event.key === "Home" ? 0 : event.key === "End" ? end : (inspectedBar < 0 ? end : inspectedBar) + (event.key === "ArrowLeft" ? -1 : 1));
    });
    document.querySelectorAll(".desk-scroll").forEach((el) => {
      el.tabIndex = 0;
      el.setAttribute("role", "region");
      el.setAttribute("aria-label", el.closest(".desk-panel")?.querySelector("h3")?.textContent || "Market data");
    });
    syncView("overview");
  }

  function hideBar() {
    $("chartTooltip").hidden = true;
    $("chartCrosshair").hidden = true;
  }

  function showBar(index) {
    const rows = lastData?.chart;
    if (!rows?.length) return;
    inspectedBar = Math.max(0, Math.min(rows.length - 1, index));
    const row = rows[inspectedBar];
    const tooltip = $("chartTooltip");
    const heading = document.createElement("strong");
    heading.textContent = `${lastData.symbol} · ${row.date}`;
    tooltip.replaceChildren(heading);
    const labels = locale === "en" ? ["Open", "High", "Low", "Close", "Volume"] : locale === "zh-cn" ? ["开", "高", "低", "收", "量"] : ["開", "高", "低", "收", "量"];
    ["open", "high", "low", "close", "volume"].forEach((key, i) => {
      const cell = document.createElement("span");
      cell.textContent = `${labels[i]} ${row[key] == null ? "-" : Number(row[key]).toLocaleString(locale === "en" ? "en-US" : "zh-TW", { maximumFractionDigits: 2 })}`;
      tooltip.append(cell);
    });
    tooltip.hidden = false;
    const crosshair = $("chartCrosshair");
    crosshair.style.left = `${52 + inspectedBar * ( $("chart").getBoundingClientRect().width - 122) / Math.max(1, rows.length - 1)}px`;
    crosshair.hidden = false;
  }

  return { boot, translate, queryState, syncView };
})();
