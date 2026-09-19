# UI component review — 19 September 2026

Scope: landing page, shared controls, sign-in and consent, existing cash-chart and planner interactions, and the two app empty states. The separate dashboard redesign is being handled in the forked session.

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
