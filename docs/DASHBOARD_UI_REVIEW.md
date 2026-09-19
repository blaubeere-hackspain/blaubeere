# Dashboard design review — 19 September 2026

Scope: app shell, sidebar, login, and painted empty states. References: the user’s Ace Studio sidebar/full-app screenshots and Handhold cards. Applied Emil design engineering and Better UI guidance. Existing chart calculations, planner behaviour, and financial data remain unchanged from main.

## Structure and surfaces

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| MEDIUM, fixed | `apps/app/app/globals.css:59`, `apps/app/components/sidebar.tsx:12` | Narrow divided sidebar, prominent callout, square selected row | Light gray 288px sidebar, 44px rounded navigation rows, quiet company group and compact assistant action | Match the reference hierarchy while keeping every navigation action real. |
| MEDIUM, fixed | `apps/app/app/globals.css:89`, `apps/app/app/dashboard/page.tsx:66` | Flat edge-to-edge header and page | White inset content panel, 20px inner/24px outer corners, thin header | Concentric corners and restrained depth separate navigation from content. |
| MEDIUM, fixed | `apps/app/app/globals.css:218`, `apps/app/components/painted-empty-state.tsx:4` | Empty-state copy deliberately overlapped the image | Image and copy stack in normal flow, with 16px text gaps and explicit heading line height | Removes the Mac overlap reported during review. No negative margin or text overlay remains. |
| LOW, fixed | `apps/app/components/auth-layout.tsx:5`, `apps/app/app/globals.css:21` | Solid color panel with a decorative chart | Equal-width garden painting and sign-in form; broad white story card over the painting | Use the requested original oil imagery and serif treatment while keeping form labels clear. |

## Typography and interaction

| Severity | Location | Before | After | Why |
| --- | --- | --- | --- | --- |
| MEDIUM, fixed | `apps/app/app/layout.tsx:1`, `apps/app/app/globals.css:16` | IBM Plex throughout navigation and forms | Self-hosted Inter for app controls; serif brand and editorial headings; existing chart fonts retained | Consistent neutral typography follows the sidebar reference without changing financial displays. The exact screenshot font was not identifiable. |
| MEDIUM, fixed | `apps/app/app/dashboard/page.tsx:68`, `apps/app/app/globals.css:101` | Decorative sidebar icon; sidebar removed on small screens | Working desktop collapse button and native modal navigation on mobile | Named controls expose the state; native dialog supplies focus containment, Escape, and focus restoration. |
| LOW, fixed | `apps/app/components/sidebar.tsx:25` | Company selection only in the page toolbar | Actual authorised companies are also available in the sidebar, with pressed-state indication | Keeps company context accessible and avoids invented projects or recent items. |

## Verification

Passed: app TypeScript check, web checks, and optimized Next.js production build. Desktop Chrome on macOS: garden login rendering, successful sign-in against a separate local demo database, dashboard/chart rendering, pointer collapse and keyboard restore of the sidebar, opening connections, and rendering the garden empty state.

Code inspected: disabled/loading states, named navigation and close controls, 44px hit areas, retained native dialog behaviour, responsive sidebar/login CSS, image sizes, explicit heading line height, normal-flow empty-state layout, fine-pointer hover gating, and reduced-motion handling. Sidebar layout changes are immediate; the icon crossfade is disabled for keyboard focus.

Not verified: the final overlap fix in a fresh browser capture, mobile drawer interactions and mobile visual layout, successful clipboard write feedback, Safari/Firefox, screen-reader hardware, and animation replay at 10% speed. Browser connector initialization failed; native controls permitted part of the desktop check but active-tab changes interrupted further verification. These limits are not a claim of passing coverage.

Approve for the inspected code and desktop coverage. Remaining verification is listed above.
