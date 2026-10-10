# OpenKiri UI Criteria

This is the acceptance baseline for the October 2026 workspace redesign. It adapts established product patterns to stock research; it is not a claim of full WCAG certification or affiliation with the referenced companies.

## References

- [IBM Carbon: UI shell](https://www.carbondesignsystem.com/building-blocks/core/components/ui-shell-header/guidelines): persistent product identity, predictable primary navigation, and global utilities grouped at the right.
- [IBM Carbon: data tables](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines): scan-friendly data, related actions near their table, consistent alignment and spacing.
- [IBM Carbon: empty states](https://www.carbondesignsystem.com/building-blocks/core/patterns/empty-states): distinguish unavailable data from a valid empty result and provide an actionable recovery.
- [WCAG 2.2 target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum): support reliable pointer interaction. OpenKiri targets 44px primary controls, exceeding the 24px AA minimum where applicable.
- [WCAG focus visibility](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible): keyboard users must be able to identify the focused control.

## Acceptance Criteria

| Area | Required behavior |
| --- | --- |
| Navigation | Overview, T+0, screener, and news occupy stable positions. Secondary destinations live in More. Exactly one notification entry. |
| Information hierarchy | Search, quote summary, and chart precede advanced analysis. Opening a destination displays its content without a long scroll through unrelated sections. |
| Stock identity | A manual query immediately clears the preceding quote. Only the latest matching response can populate the page. |
| Request states | Loading names the requested symbol. Provider failure shows Retry. Empty chart data has its own message. Requests time out after 30 seconds. |
| Chart | Indicators retain their saved state. Hover, touch, or keyboard inspection exposes date and OHLCV values. Clicking the chart does not unexpectedly navigate away. External providers have explicit links. |
| Progressive disclosure | Signals, model details, relationships, cost calculations, and advanced checks expand on demand. Existing functions remain accessible. |
| Responsive layout | No document-level horizontal overflow at 320, 390, 768, or 1440px. Wide data tables scroll inside labeled, keyboard-focusable regions. |
| Accessibility | Named form fields and icon buttons, visible focus, skip link, native disclosure controls, Escape dismissal, reduced-motion support. Primary controls have 44px target height. |
| Data cost | No new polling introduced. Overview does not load market scanners, recommendations, or event feeds. Native T+0 controls have a single request owner. |
| Visual language | Neutral charcoal surfaces, mint actions, restrained borders, stable typography, and market-specific red/green values with direction symbols. No floating tools covering data. |

## Verification

`pnpm install --frozen-lockfile`, `pnpm exec playwright install chromium`, then `pnpm test:ui` run synthetic-data browser checks without financial-provider calls. Set `PLAYWRIGHT_CHANNEL=chrome` to use a locally installed Chrome. `UI_SCREENSHOTS` optionally sets a screenshot output folder.

The checks cover all viewport sizes above, nonblank canvas content, view switching, duplicate requests, query races, mismatched symbols, failure recovery, empty data, language changes, indicators, keyboard inspection, and watchlists. Real-provider latency and freshness remain separate backend concerns.

Icons are a locally hosted subset of Lucide 0.468.0; see `static/lucide-LICENSE.txt`. No external font or icon CDN is required.
