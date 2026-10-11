# Data and Telemetry Policy

## Identity and Time

Yahoo chart results must contain the requested symbol in `meta.symbol`. Quote results are selected by symbol, not by array position. Summary valuation requires `price.symbol` to match. A bare four-digit Taiwan code may resolve to TWSE or TPEx; explicitly suffixed symbols must match that exchange.

`as_of` is the source timestamp in UTC. Chart timestamps denote the start of the bar, not the fetch time or a guaranteed last-trade time. `fetched_at` is the actual provider retrieval time and survives cache reuse. The UI displays the viewer's time zone. A quote refresh cannot replace a more recent displayed observation with an older one.

Valuation source fields do not necessarily supply a separate publication timestamp. In that case `as_of` stays null and the UI says the source did not provide the time; a quote timestamp is not substituted. `quote_as_of` is retained separately when supplied.

## Valuation

Reported market cap requires verified symbol and currency. Missing cap/P/E remains unavailable, without a built-in static estimate or a fixed FX conversion. Negative or nonfinite ratios are not presented as a valid positive P/E. Cross-market screener ordering converts sourced TWD market cap using cached Yahoo TWD/USD chart data and retains `fx_as_of`; absent values sort last. The built-in universe still selects candidate pools and contributes to pre-existing model scores; it is not a current valuation source.

The deferred valuation endpoint uses these public inputs before the legacy Yahoo quote fallback:

- Taiwan: [TWSE OpenAPI](https://openapi.twse.com.tw/) `t187ap03_L` / `BWIBBU_ALL`, and [TPEx OpenAPI](https://www.tpex.org.tw/openapi/) `tpex_daily_market_value` / `tpex_mainboard_peratio_analysis`. P/E is the exchange's daily reported value. Listed market cap is an **estimate**, current available Yahoo chart price multiplied by the profile's issued common shares, not paid-in capital divided by an assumed par value. Profile snapshot date and price observation time are retained separately. TPEx market cap is the dated official closing value in millions, converted to TWD and checked against the same row's issued shares times closing price. TDR shares are not used for estimates; ETFs lacking company profiles remain unavailable.
- US: [Nasdaq US-stock summary](https://www.nasdaq.com/market-activity/stocks/mu) supplies reported USD market cap for the exact ticker. Its separate valuation publication time is unknown and remains null. The public website endpoint has no guaranteed service level.
- US P/E: Yahoo's public `fundamentals-timeseries` `trailingDilutedEPS` provides a dated TTM EPS input. P/E is an **estimate**, price divided by that EPS, only when both currencies are USD and the EPS period does not follow the price observation. ADR/depositary names are excluded because underlying-share EPS can differ from depositary-unit EPS. This calculation is not a forward P/E or a promise to match Google's vendor methodology.
- Each available field carries its own `fields` provenance, estimate flag, and date. Latest zero/negative EPS leaves P/E unavailable rather than selecting an older profitable period. Price/official daily ratios older than 14 days, share snapshots older than 30 days, and TTM EPS older than 400 days are rejected. These are maximum age guards, not guarantees that a provider has published all newer filings.

Three public reads run concurrently, each with bounded connect/read timeouts. Authenticated Yahoo crumb/summary requests no longer run in the interactive valuation path. A missing upstream leaves the value unavailable rather than blocking the chart. Quotes and historical analysis do not trigger this extra fundamental download for every stock refresh.

Market cap changes are no longer copied from stock-price percentage changes, and previous market cap is not reverse-engineered from those percentages. A cap movement arrow requires actual comparable market-cap observations; this version therefore leaves it unavailable. Market-cap movement does not establish money flow.

Google Finance remains an external comparison link. Automatic page scraping is removed from the quote and valuation path because the existing parser could mix page content and did not establish source timestamps. Google's [finance disclaimer](https://www.google.com/googlefinance/disclaimer/) describes provider delays and data limitations. No promise of broker-grade live data is made.

## Sessions and Data Age

Analysis and quotes include `data_status`, computed from their existing source metadata. Yahoo `currentTradingPeriod` epoch windows are preserved, including exchange time zone and any supplied shortened session. A recent explicit `marketState` can establish the provider's state for at most five minutes; conflicting regular-state/session evidence is unconfirmed. Cached API replies recalculate these labels without fetching another calendar or quote.

Supported Taiwan/New York conventional-session weekends can be labeled closed in the exchange time zone. Weekday closure is inferred only outside an actual supplied window on that same exchange date. A previous day's window is not copied onto today's weekday, and missing holiday/exception information remains **unconfirmed**. This is not a complete official holiday calendar or exchange halt feed. Official regular-session references: [TWSE](https://www.twse.com.tw/zh/products/system/trading.html) and [NYSE](https://www.nyse.com/trade/trading-information).

Observation labels never imply verified real-time delivery. Quotes/intraday bars are considered older after the greater of 15 minutes or two returned bar intervals. A daily bar's start time is not assessed as a one-minute trade timestamp; daily/weekly historical bars use seven/28-day age guards. During a confirmed closed state, observations matching the last supplied session (within seven days) remain labeled as last-session data, rather than a live intraday delay. Regular-session quotes in pre/post-market are labeled regular-session-only, not extended-hours prices. Missing and future timestamps are visible warnings.

A requested intraday chart that falls back to daily bars gets a dedicated warning. Quote refresh and historical chart quality are shown separately. The UI says **page loaded**, not quote updated, for its own render time. Market-state labels expire at the next supplied boundary or within five minutes; a local 30-second display check replaces expired labels with a refresh-needed state, without network requests. Data-age thresholds are display heuristics, not a trading SLA or proof a security is tradable.

### Intraday Candidates

The T+0 model uses the metadata already returned by each scanned chart; it does not fetch a second quote or a calendar. Active candidates require a confirmed regular session with a supplied session window, an intraday interval, a nonfuture observation inside that window, and age within the existing freshness limit. The same score threshold used by `side` (at least +8 / at most -8) determines the long/short groups; weak scores remain observation-only. Daily fallbacks, missing/invalid times, unknown sessions, extended-hours data and delayed observations are excluded from active candidates.

Confirmed closed-session observations matching the last supplied session appear only in separate **reference** groups. Daily references retain their actual daily interval and label. Older closed data is excluded, not promoted into a reference candidate. These classifications do not establish tradability, short-sale eligibility or an entry recommendation.

`/api/movers` exposes candidate and reference arrays, eligibility/quality metadata, source times, counts, aggregated exclusion reasons and per-market session summaries. A market is confirmed closed only when all scanned observations agree; empty or conflicting evidence stays unknown, independently of model scores. Cached raw observations are reclassified at request time without another scan; scan completion and source times are preserved. Active validity ends at the earlier of the session/state boundary and the observation-age limit. The existing local display timer removes expired rows without network I/O. Frontend market-switch requests are abortable and sequenced, failed refreshes clear old candidates, and no general gainers/losers list substitutes for an empty candidate group. Scan caps, cache TTLs and the active-market polling cadence are unchanged.

T+0 automatic requests stop outside its workspace and in hidden browser tabs. Confirmed closed markets are skipped by automatic model-board and quote refreshes; mixed-market views preserve dated references from skipped markets. Expired closure evidence changes the badge to confirmation-needed but does not guess a reopening or resume polling. Returning to the foreground/T+0 workspace or a manual refresh probes all selected markets again. Unknown markets are not paused merely because candidate lists are empty. Slow automatic batches are coalesced; leaving/hiding the workspace or switching markets aborts pending browser requests and ignores their late results. Aborting cannot reclaim provider work already issued by the server or previously billed traffic.

### On-Demand Stock Details

`/api/daytrade/analyze` is now called only by selecting a quote-board ticker or explicitly refreshing that stock's model. It is never called by entering T+0, quote/model-board refreshes, chart rendering, language changes, returning from the background, or expanding the details disclosure, regardless of data-saver mode. Repeated selection of an already loaded snapshot reuses it; repeated selection during its pending request is coalesced. Chart navigation is a separate command, so selecting details does not also download a historical chart or valuation.

Stock details occupy the full workspace width directly below the quote board. Desktop metrics use three columns, with two columns on smaller screens; the selection scroll target leaves room for the sticky navigation.

The selected symbol is verified against both the model response and its quote. Switching symbols clears previous results before loading; changing markets, failure, cancellation and cost-input changes cannot leave another stock's visible model. Client load time, model bar start, quote observation and benchmark observation are displayed separately. Missing source times remain unavailable. Detail metrics no longer require calculating every quote-board stock; they live in the selected snapshot. Model-provided costs are rendered without a redundant cost API call. Late standalone cost responses cannot overwrite inputs for a different stock or scenario.

The detail model has stricter validity rules than the informational quote board: quotes and benchmark observations must be less than 120 seconds old; model bars must be younger than two intervals plus 60 seconds (180 seconds for 1m, 660 for 5m). Any supplied positive provider delay also blocks interpretation. All three require a confirmed regular window, a nonfuture source timestamp and supported matching instrument/currency. Yahoo trade snapshots without a window can reuse the same stock chart's window, not its timestamp or a different exchange's timezone. Historical/nonregular bars are excluded from intraday metrics; invalid OHLC/volume stops the model. Missing bars are never synthesized with the current time.

`execution.allowed` is an application display gate, **not permission or a recommendation to execute a trade**. Blocked replies keep identity and provenance but return null score/cost/playbook and empty levels. Benchmark failure is not converted to a neutral index. The stock and benchmark are rechecked after provider work completes, and the earliest age/state/session boundary becomes `execution.valid_until`. The browser removes expired model content locally, including after sleep/background throttling, and never refreshes it implicitly. Chart navigation and manual retry remain available.

The selected-model route offloads synchronous provider/model work to Starlette's bounded worker pool rather than blocking the ASGI event loop. This keeps unrelated async requests schedulable while a provider is slow; it does not make the provider respond faster, add polling, or cancel upstream work already issued. All provider reads still use the existing coalesced caches and timeouts.

### Market and Cost Scenarios

The chosen broad-market context is `^TWII` for listed Taiwan equities, `^TWOII` for `.TWO` stocks and `^GSPC` for US stocks. This is broad-market context, not sector beta or a verified market-wide model. Benchmark identity, time and regular-session quality must qualify independently. ETF/US scenarios do not inherit Taiwan equity tax defaults.

Costs support **whole-share long positions** only. Taiwan equity defaults are a user-editable conditional day-trade tax scenario (sell tax 0.15%; normal equity sales 0.3%), fee rate 0.001425 and minimum TWD20, not a claim that a broker charges those commissions or that a security/account qualifies. The reduced tax currently runs through 2027-12-31; see the [TWSE investor guide](https://www.twse.com.tw/zh/about/company/guide.html). Taiwan stock price bands follow the [TWSE trading rules](https://www.twse.com.tw/zh/products/system/trading.html).

US requires explicit fee rate, minimum fee, other sell-side rate, price increment and confirmation. Zero is allowed only as an entered scenario, never assumed from blank fields. USD fees round to cents; TWD fees round to whole currency units using the chosen floor/nearest/ceiling mode. Minimum and additional fee inputs must use currency minor units. The additional sell fee represents an amount for the entered quantity; per-share charges must be incorporated by the user. Unentered fees, borrow/margin costs, FX, spread and slippage are not included. US price increment defaults to an editable 0.01 scenario, not a universally verified legal tick; the SEC's revised tick implementation schedule was [further extended in June 2026](https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf). Broker trading/account requirements are deliberately not hardcoded; see [FINRA intraday trading guidance](https://www.finra.org/investors/insights/frequent-intraday-trading).

Taiwan ETF price increments use 0.01 below TWD50 and 0.05 at/above TWD50. ETF sell tax is **not inferred** from the generic ETF type: the user must supply and confirm the rate because equity/bond ETF treatment differs; see [TWSE ETF rules](https://wwwc.twse.com.tw/zh/products/securities/etf/overview/rules.html). Switching stocks revokes confirmation; switching market/type resets incompatible defaults. Returned quote type is preserved when prices populate the form.

`openkiri_cost.py` is a shared pure cost module, not another deployment entry. Decimal arithmetic and monotone binary search calculate break-even prices without the former 4,000-tick limit, including Taiwan graduated-band boundaries. Tick counts are an initial-band reference, not the exact number of exchange ticks across bands. Rounded net P/L can be zero while exact P/L is slightly negative; risk gates use the sign of unrounded net return. None of these calculations confirm order availability, execution prices, shortability, settlement or broker eligibility.

## Request Cost

Default analysis fetches chart data and runs the technical model. Valuation is fetched separately, so it does not block the chart. Users can request news/event analysis using `context=true`. Price refresh and chart data keep separate provenance; quote refresh does not rewrite historical bars.

Public provider GETs are coalesced using bounded locks. Successful reads use the TTL policy; selected failure responses are cached for at most 60 seconds. Official Taiwan bulk datasets and Yahoo EPS inputs are cached for six hours, shared across users; Nasdaq summaries and completed valuations are cached for 15 minutes per resolved symbol, including unavailable results. UI valuations cache for five minutes. Presets and diagnostics add no recurring polling. Existing data-saver cadence remains in place.

## Diagnostics

`GET /api/diagnostics` returns in-memory process-lifetime counters. The UI requests them only when the diagnostics disclosure opens or its refresh command is used.

- API: response body bytes after gzip, average duration, recent P95, and errors. Response headers and TLS are excluded.
- Upstream: estimated HTTP request bytes, decoded response payload bytes, duration, and errors. These are observations from this process, not Render billing totals.
- Cache: hit percentage per instrumented layer. API/provider rows show no cache percentage because hits are measured separately.
- P95 uses a rolling maximum of 128 samples per group. Average and counters cover the process lifetime. Group count is bounded at 64.
- Labels contain only provider categories and route templates. No ticker, query string, IP, cookie, credential, or request body is retained.
- Counters reset on restart and are separate for each worker. Incoming provider payload sizes must not be interpreted as billed outgoing traffic. Render's [outbound-bandwidth documentation](https://render.com/docs/outbound-bandwidth) describes its billable categories.

## Presets

Intraday defaults to Today / 5m with MA5/20/25, volume, and crosses. Swing defaults to 6mo / daily with MA5/20/25/60, volume, crosses, and MA25 bias. BUY markers default off. Users can save their own settings for each mode on the current device, including the T+0 market and expanded analysis sections. Watchlist storage remains shared between modes.

MA25 on a 5m chart is 25 five-minute bars, not 25 trading days. The bias label follows the actual returned interval, including fallback intervals. A daily chart is required for the 25-day bias view. Presets do not enable automatic refresh or place orders.

## Verification

Offline Python tests cover identity mismatch, source time preservation, raw financial fields, missing valuation, concurrent cache misses, denied-request caching, technical-only analysis, and bounded telemetry. Synthetic browser tests cover late valuation responses, unavailable fields, opt-in diagnostics, mobile overflow, preset restoration, and context analysis. Live-provider latency varies and should be measured separately from these deterministic checks.
