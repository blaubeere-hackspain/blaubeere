# UI component review — 19 September 2026

Scope: landing page, shared controls, sign-in and consent, existing cash-chart and planner interactions, and the two app empty states. The initial pass left the dashboard redesign to the forked session. The final typography pass below includes that session’s merged monthly model dashboard.

The visual direction follows the user’s oil-painting and Handhold screenshots: original painted landscapes, broad pale surfaces, serif display copy and quiet supporting text. The landing component review preceded the Interfaces.dev polish pass.

## Responsive composition and honest affordances

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| MEDIUM | `apps/landing/app/page.tsx:41`, `apps/landing/app/globals.css:75` | Three feature cards under the hero did not show the product or match the requested proportions. | One broad workspace preview with a 36px outer radius, 26px inner radius and 10px inset; 30/22/8px on phones. | Concentric corners and a clear product demonstration make the surface coherent. |
| MEDIUM | `apps/landing/app/page.tsx:60`, `apps/landing/app/globals.css:127` | Scaling the entire desktop chart made phone labels too small. | A compact SVG layout preserves native label size; the desktop chart is hidden on phones. | Readable numbers carry more value than preserving a desktop composition. |
| MEDIUM | `apps/landing/app/page.tsx:49` | A static forecast label suggested a dropdown. | Plain “90-day outlook” status label; actual navigation remains a link. | An affordance should match an available action. |
| MEDIUM | `apps/landing/app/page.tsx:29`, `apps/landing/app/globals.css:132` | The requested three-image editorial section was missing. | Three original portraits, followed by an offset statement about the product. | The page now follows the supplied composition without copying its imagery or text. |
| LOW | `apps/landing/app/globals.css:135`, `apps/app/app/globals.css:229` | Pale artwork edges could blend into their surface; the first outline used a tinted neutral. | A 1px pure-black outline at 10% opacity. | A neutral image edge adds definition without discoloring the painting. |
| MEDIUM | `apps/landing/app/page.tsx` — `#assistants` | MCP capabilities had no dedicated landing explanation; the initial landscape was too abstract. | A dedicated oil painting depicts a chat assistant connected through MCP to records, cash and scenario charts, beside three separated benefits. Its 3:4 frame preserves the whole illustration on phones. Copy covers capabilities, compatibility and access limits. | Both the illustration and text explain what a connected assistant does before visitors open the app. |

## Surfaces, typography and motion

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| MEDIUM | `apps/landing/app/globals.css:6`, `apps/landing/app/globals.css:28` | The page lacked the full painted environment and display typography in the reference. | Edge-to-edge oil painting, large serif heading and a white gradient; supported browsers also fade the painting with scroll. | The atmosphere extends across the opening section and resolves into readable white space. |
| MEDIUM | `apps/app/app/globals.css:228`, `apps/app/components/connections.tsx:52`, `apps/app/app/dashboard/page.tsx:69` | Empty states were generic icon boxes. | Original paintings, 28px outer / 20px inner corners with 8px padding, serif titles, a clear explanation and an available next action. | The empty states explain how to proceed and share the landing’s visual language. |
| LOW | `apps/theme.css:40`, `apps/theme.css:50`, `apps/landing/app/globals.css:146` | Inconsistent text wrapping and flat surfaces. | Balanced headings, pretty paragraph wrapping, tabular figures and restrained layered shadows. | Typography remains readable while structural borders and elevation have distinct roles. |
| LOW | `apps/landing/app/globals.css:147`, `apps/theme.css:91` | The hero arrived without a visual sequence; controls lacked consistent press and state feedback. | A short, staggered hero entrance; 0.96 pointer-press feedback; persistent contextual icons using opacity, scale and blur transitions. | Infrequent entrances establish hierarchy. Frequent chart and keyboard interactions remain immediate. |

## Interaction continuity, labels and recovery

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| HIGH — fixed | `apps/app/components/planner.tsx:21`, `apps/app/components/planner.tsx:30` | Comparing a plan could update the chart behind the dialog; an outstanding request could finish after closing it. | Abort on close/unmount, ignore aborted responses, and apply a comparison only through an explicit preview action. | Results should not move focus or change a user’s working context after dismissal. |
| MEDIUM | `apps/app/app/dashboard/page.tsx:43` | Changing the horizon removed the current assessment and disclosure state. | Keep the same company’s assessment visible with an updating status; clear it when switching companies. | Preserving context avoids unnecessary layout changes while making stale data explicit. |
| MEDIUM | `apps/app/components/cash-chart.tsx:6`, `apps/app/components/cash-chart.tsx:58` | Irregular ticks and a small inspection control made values harder to read and reach. | Rounded cash ticks, fewer date labels on phones, a 44px range control, spoken date/value text and a “Lowest cash” action. | Cash inspection works with pointer, touch-sized controls and keyboard input. |
| MEDIUM | `apps/app/components/dialog.tsx:4`, `apps/app/components/planner.tsx:10` | Backdrop handling could confuse dialog padding or a selection drag with dismissal; numeric limits were unclear. | Dismiss only when a pointer starts and ends outside the dialog; explicit field labels and native numeric bounds. | Native dialog behavior preserves focus handling; validation prevents avoidable failed requests. |
| MEDIUM | `apps/app/components/connections.tsx:28`, `apps/app/components/connections.tsx:46` | Endpoint copying lacked a clear empty-state action and consistent feedback. | Both copy controls use the same handler, a mounted Copy/Check icon pair and an announced success message; failures provide a manual-copy path. | State remains understandable without depending on motion. |

## Verification

- Both production frontend builds, TypeScript checks, web checks and deployment-script checks pass locally. The runnable checks cover money input, chart ranges including flat zero, dialog boundaries, redirect validation, the required Jio account, stopped-VM recovery and invalid deployment inputs.
- Landing inspected in Chrome at desktop width and 390×844: hero, scroll fade, mock dashboard, mobile chart, three-painting gallery, editorial copy, planning panel, trust section and footer. No horizontal overflow was observed. The mock is clearly labelled as illustrative data.
- MCP landing section inspected in Chrome at 1800px and 390×844: loaded painting, three visible feature rows with separators, no horizontal overflow, and a working CTA to the dashboard. The replacement MCP illustration was rechecked at both sizes: the assistant, connector and all three financial sheets stay visible. Landing TypeScript and production build pass. Production smoke checks require all five landing paintings.
- Sign-in inspected on mobile and with keyboard input: explicit password label, visible focus and immediate keyboard-triggered visibility changes.
- Existing planner/chart checked: native bounds, comparison results, explicit preview, Escape dismissal, restored trigger focus, body scroll lock, retained disclosure state when changing horizon, lowest-cash selection and arrow-key inspection.
- Assistant empty state inspected at desktop width and 390×844. Its painting loads, the dialog stays within the viewport, and the visible copy-success message was verified after clicking “Copy endpoint.”
- Code inspection covers reduced-motion media queries, fine-pointer hover gating, explicit transition properties, decorative empty-state image alternatives, consent loading/error/disabled states and the no-company state.

**Not verified:** Safari/Firefox rendering; actual touch hardware; a screen-reader session; motion replay at 10% speed in DevTools; browser-emulated reduced motion; a deliberately throttled request/close race; the no-company state in a signed-in browser. These are coverage limits, not claims of completed testing.

## References and assets

Applied [Emil’s design engineering guidance](https://skills.sh/emilkowalski/skills/emil-design-eng) and [Better UI](https://skills.sh/jakubkrehel/skills/better-ui). The Interfaces.dev pass used [Details that make interfaces feel better](https://interfaces.dev/magazine/issues/details-that-make-interfaces-feel-better) for the small surface, type and state refinements, and [shared layout animation guidance](https://interfaces.dev/magazine/issues/how-i-use-shared-layout-animations) to consider continuity. Existing DOM and native CSS were sufficient; no animation dependency was introduced.

The scroll-linked effect has a static gradient fallback and is disabled for reduced motion. Browser support was checked against [MDN’s animation-timeline documentation](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/animation-timeline).

Original asset paths and generation prompts/briefs are recorded in [ARTWORK.md](ARTWORK.md).

**Approve** for the inspected scope. No unresolved HIGH finding was identified; the unverified checks above remain explicit.

## Published model dashboard review

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| HIGH — fixed | `components/model-dashboard.tsx` | Published model records were unavailable in the app. | Dated monthly ratings, original model inputs and reason codes, visible confidence, null states and source provenance. | Explain the returned rating without inventing daily scores, a bank balance or numerical attribution. |
| MEDIUM — fixed | `components/sidebar.tsx` | Company buttons assumed a small workspace. | Native labelled select for large lists; overview label follows the selected data source. | The imported company universe stays navigable without expanding the sidebar indefinitely. |

Regression checks render missing-score overview/detail pages and verify source gaps, monthly labels and dated links. Existing surface styles, keyboard-accessible disclosures and links are reused. Chrome inspection passed at desktop width and 390×844: team sign-in, the imported company selector, monthly history, the score-to-explanation link, responsive metric cards, input rows and reason text. No overlap was observed in those views. The Next.js production server also passed API/OAuth/MCP integration checks using the Rust dataset snapshot. Safari/Firefox and assistive-technology checks remain unverified.

## Final typography and component pass

Applied [Jakub Krehel’s better-typography](https://github.com/jakubkrehel/skills/blob/main/skills/better-typography/SKILL.md) and [better-ui](https://github.com/jakubkrehel/skills/blob/main/skills/better-ui/SKILL.md), with their accessibility and layout guidance. This pass supersedes the earlier static MCP illustration and story ordering: the story now follows the dashboard preview, followed by the gallery; hover, focus or tap on the three MCP features selects the matching oil painting.

### Readable type and stable numbers

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| HIGH — fixed | `apps/app/components/model-dashboard.tsx:17` | A 1000-unit SVG shrank 12px labels to about 4px at a 342px rendered width. | ResizeObserver matches the SVG coordinate width to its rendered width; two date labels on narrow screens, three on desktop. | Axis type remains 12px and dates do not collide. Missing ratings still break the line. |
| MEDIUM — fixed | `apps/theme.css:18`, `apps/theme.css:71`, `apps/app/app/layout.tsx:1` | Financial values used a compressed monospace face with an unloaded 500 weight; app charts used a second sans family. | Shared type roles, the existing UI font with tabular digits, actual loaded weights, and the same font for chart labels. Monospace is retained for model identifiers. | Values stay aligned and readable without synthesized emphasis or unnecessary font downloads. |
| MEDIUM — fixed | `apps/landing/app/globals.css:28`, `apps/landing/app/globals.css:163`, `apps/landing/app/globals.css:194` | The phone headline broke into four tightly stacked lines; story copy had tight leading and prices used the display serif. | A smaller fluid headline floor, looser display tracking, 1.4 story-heading leading, 1.6 body leading, a 65ch measure and clean tabular prices. | Preserve the painted editorial direction while making the content easier to scan. |
| MEDIUM — fixed | `apps/app/components/painted-empty-state.tsx:4`, `apps/app/app/globals.css:230` | The assistant empty-state heading was another h2 and could overpower its dialog title. | Nested h3 and a smaller serif size, with less empty top spacing. | Heading semantics and visual hierarchy agree. |

### Components and reflow

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| MEDIUM — fixed | `apps/app/app/globals.css:131`, `apps/app/app/globals.css:162` | Checkpoint columns pushed the 320px document to 342px; four metrics became cramped beside the sidebar. | Shrinkable grid children, compact checkpoint rows below 380px and two-column metrics where the sidebar leaves insufficient space. | Complete amounts remain available without page-level horizontal scrolling. |
| MEDIUM — fixed | `apps/app/components/model-dashboard.tsx:42`, `apps/app/components/sidebar.tsx:27`, `apps/theme.css:111` | New selectors looked like loose text; several native selects stayed below 16px on mobile. | Reuse the shared native control surface and 16px mobile input size. | Controls read as interactive and avoid iOS focus zoom. |
| LOW — fixed | `apps/theme.css:64`, `apps/landing/app/globals.css:197` | Secondary buttons used solid depth borders; feature lists had excess internal spacing. | Existing transparent shadow tokens, lighter regular-text icons and tighter pricing lists. | Surfaces and optical weight follow the same component language. |

Verification: both frontend production builds and web checks pass. Chrome checks covered the landing hero, MCP selection, pricing cards/table, cash dashboard, dated health page, planner and assistant empty state at widths from 320px to 1800px, including the 1024px sidebar breakpoint. The monthly model overview and detail were inspected with 24 imported synthetic records in a temporary local preview; the preview was removed before build/commit. At 320px, both dashboard variants have document width equal to viewport width. At 390px the model SVG viewBox and rendered width both measure 342px, keeping text at its intended size. Keyboard focus and Escape restoration were checked in the assistant dialog; MCP changes with focus and tap. The regression check verifies readable date-label density and preserves gaps for missing model ratings.

Not verified in this pass: Safari/Firefox, physical touch devices, a screen-reader session, automated accessibility audit, 200% browser zoom, RTL, and 10%-speed animation replay. Reduced-motion behavior was checked in code.

**Approve** for the inspected typography/component scope; no unresolved HIGH finding.


## Monthly cash, payment and debt views

- Added native month and chart-mode selectors, signed cash categories, distinct supplier-payment/customer-collection sections, rolling debt-service totals and a keyboard-scrollable history table. Existing cards, typography and chart scaling are reused.
- Financial values retain EUR precision; missing months split the cash line. Labels distinguish cumulative movements from bank balances, invoice cutoff totals from monthly flows, and observed debt service from outstanding debt.
- Fixed the mobile chart header’s inherited flex basis, which created unnecessary vertical space when its controls stacked.

Verification: web regression checks cover exact amounts, negative outflows, separate payment/collection arrears, incomplete evidence, empty cash history and gaps in the plotted series. Chrome checks with the Rust-imported data covered month/table selection, dated score links, cumulative chart mode, unavailable cash with available invoices, company changes, and 320px/390px layouts. Document width remained equal to viewport width at 320px; the records table scrolls within its card. TypeScript and the production app build pass. Safari/Firefox, physical touch and a screen-reader session remain unverified.

## Landing story and scroll motion

| Location | Before | After |
| --- | --- | --- |
| Product story | Label/logo, first-line indent and a narrower paragraph measure | Label/logo removed. An 800px column meets the right content edge, with matching left-aligned heading and paragraph widths. This supersedes the earlier 65ch body measure. |
| Landing and pricing | Below-the-fold sections appeared at once | One-time 420ms opacity/16px rise reveals, with 60ms group staggering. Native IntersectionObserver; no new dependency or dashboard animation. |

Verified in Chrome at 1800px and 390px: first entry, staggered paintings, no replay when scrolling back, immediate keyboard focus, pricing-table entry and no horizontal overflow. Story alignment also checked at 320px. Web checks cover initial visibility, focused content, observer cleanup, late observer callbacks, changing reduced-motion preferences and unsupported browsers. Landing production build passes. Server markup remains visible without JavaScript; reduced-motion and print CSS keep it static. OS/browser-emulated reduced motion and print rendering remain unverified.


## Imported-company demo and health-first dashboard

This pass replaces the earlier offline Mediterránea demo. The public `/demo` now reads the published challenge snapshot from Rust, using the same joined records and model component as the private workspace. The fixture bundle and its exporter were deleted. The user explicitly requested all imported companies in the demo.

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| HIGH — fixed | `apps/app/app/demo/page.tsx`, `apps/app/components/dashboard.tsx` | Access demo opened one hardcoded company and forecast. | All imported companies, server-supplied monthly data, explicit loading/error states and no mock fallback. | The entry flow now exposes the data the user expects. |
| MEDIUM — fixed | `apps/app/components/sidebar.tsx`, `apps/app/app/globals.css` | Company selection was duplicated in the body and lower sidebar; navigation used pill surfaces. | One selector at the sidebar top, white surface, outlined icons, compact rounded active row, grouped navigation and actual recently selected companies. | Matches the supplied sidebar reference and gives company context one home. |
| MEDIUM — fixed | `apps/app/components/model-dashboard.tsx` | Health history appeared below cash and payment details with a repeated grid of date tiles. | Health score, history chart and cutoff evidence lead; cash, payment/collection timing, debt and records follow. A native month control synchronizes every section. | The published rating becomes the starting point, with evidence close at hand. |

Verification: Rust checks cover all 1,286 demo companies, exact parity with the private dataset response, explicit publication gating, a missing import, absent companies, refusal of writes and retained private authentication. Web checks cover public explanation links, chart order, missing scores, monetary values and the absence of a body company selector. Chrome checks covered company switching (including COMP_1286), public explanation/overview navigation, synchronized dates, unavailable ratings, mobile company selection and 320px/390px layouts. No document overflow at 320px. TypeScript, production build, Rust tests and Clippy pass.

Not verified: Safari/Firefox, physical touch hardware, a screen-reader session and animation replay at 10% speed. Existing reduced-motion and native dialog handling are retained. **Approve** for the inspected scope.
