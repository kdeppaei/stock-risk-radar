# Data and Telemetry Policy

## Identity and Time

Yahoo chart results must contain the requested symbol in `meta.symbol`. Quote results are selected by symbol, not by array position. Summary valuation requires `price.symbol` to match. A bare four-digit Taiwan code may resolve to TWSE or TPEx; explicitly suffixed symbols must match that exchange.

`as_of` is the source timestamp in UTC. Chart timestamps denote the start of the bar, not the fetch time or a guaranteed last-trade time. `fetched_at` is the actual provider retrieval time and survives cache reuse. The UI displays the viewer's time zone. A quote refresh cannot replace a more recent displayed observation with an older one.

Valuation source fields do not necessarily supply a separate publication timestamp. In that case `as_of` stays null and the UI says the source did not provide the time; a quote timestamp is not substituted. `quote_as_of` is retained separately when supplied.

## Valuation

Reported market cap requires verified symbol and currency. Missing cap/P/E remains unavailable, without a built-in estimate or a fixed FX conversion. Negative or nonfinite ratios are not presented as a valid positive P/E. Cross-market screener ordering converts verified TWD market cap using cached Yahoo TWD/USD chart data and retains `fx_as_of`; absent values sort last. The built-in universe still selects candidate pools and contributes to pre-existing model scores; it is not a current valuation source.

Market cap changes are no longer copied from stock-price percentage changes, and previous market cap is not reverse-engineered from those percentages. A cap movement arrow requires actual comparable market-cap observations; this version therefore leaves it unavailable. Market-cap movement does not establish money flow.

Google Finance remains an external comparison link. Automatic page scraping is removed from the quote and valuation path because the existing parser could mix page content and did not establish source timestamps. Google's [finance disclaimer](https://www.google.com/googlefinance/disclaimer/) describes provider delays and data limitations. No promise of broker-grade live data is made.

## Request Cost

Default analysis fetches chart data and runs the technical model. Valuation is fetched separately, so it does not block the chart. Users can request news/event analysis using `context=true`. Price refresh and chart data keep separate provenance; quote refresh does not rewrite historical bars.

Public provider GETs are coalesced using bounded locks. Successful reads use the existing TTL policy; selected failure responses are cached for at most 60 seconds. Fundamentals are cached for 15 minutes per resolved symbol, including unavailable results. Presets and diagnostics add no recurring polling. Existing data-saver cadence remains in place.

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
