# Original product artwork

## Vector identity

`apps/brand/blau.svg` is the shared landing, sign-in and dashboard lockup. It preserves the existing four-part berry symbol and outlines the lowercase “blau” lettering from Times New Roman Bold as SVG paths, with no font dependency or SVG text nodes. The consuming images provide accessible alternatives.

Both apps serve a white berry symbol on a black background through `app/icon.svg`, a multi-size `app/favicon.ico` and a 180px `app/apple-icon.png`.

## Social preview: final prompt

`apps/landing/public/social-preview.png` was created with the built-in image generation tool, using the outlined blau logo and the hero painting as references. The final full-bleed image is 1734 × 907 and is shared by the Open Graph and large Twitter cards across both apps.

Use case: ads-marketing.
Asset type: finished Open Graph / X large social link preview for the finance product blau.
Primary request: a beautiful, memorable full-bleed oil-painted background with very large explanatory text and a visible illustration. A refined, warm, slightly charming editorial image that stays legible as a small social card.
Input images: Image 1 is the exact blau logo reference; preserve its four-part berry symbol and lowercase serif lettering. Image 2 is an oil-painting style and palette reference, not a layout to copy.
Composition: wide 1200 × 630 landscape social-card canvas, about 1.91:1. Fill every edge with the oil painting. The left two thirds have very pale ivory sky with subtle brush texture and generous breathing room for typography. The right third shows a sunlit wooden desk by an open garden window, an open cream financial ledger and a separate cream sheet with a simple sage cash-flow line chart; soft olive foliage, quiet green hills and pale blue water behind the desk. All financial objects are distinctly hand-painted and tangible. Keep important content inside a 55px safe margin.
Brand: put the supplied dark blau logo cleanly at the top left, about 155px wide at the intended canvas size.
Headline, exact text, in two oversized lines: "Know your cash." then "Plan what’s next." Use elegant near-black Times-style serif typography, around 78px at 1200px canvas width. Make this the strongest visual element, crisp and highly legible, comfortably separated from the logo.
Supporting text, exact text, near the lower left: "Cash forecasts and what-if plans" then "for the people behind the numbers." Use clean dark sans-serif, about 26px, generous leading.
Style and mood: tactile fine-art oil on linen, warm cream, sage and olive green, muted sky blue, natural light; generous editorial composition, inviting and quietly optimistic.
Constraints: preserve exact spelling and punctuation, no additional words, no domain name, no price, no buttons, no cards framing the design, no mock social-network chrome, no watermark. Keep all text readable against the pale background. This is the final ready-to-publish image, not a mockup.

The product paintings were generated with the built-in image generation tool for Blaubeere. No source template assets or reference screenshots were copied into the application. Original PNGs live in the repository; Next.js serves optimized sizes.

## Landing paintings

| Asset | Generation brief |
| --- | --- |
| `apps/landing/public/cash-horizon-oil.png` | Luminous oil-painted lake and countryside, pale sky, sage and olive foreground, tactile brushwork. Wide background for the hero, fading into the white page. |
| `apps/landing/public/perspective-oil.png` | Portrait landscape of an alpine valley and winding lake. |
| `apps/landing/public/foundation-oil.png` | Portrait landscape with a small cream farmhouse, green hills and a muted terracotta roof. |
| `apps/landing/public/possibility-oil.png` | Portrait landscape with a person seen from behind on a bench, looking toward an open valley beneath a large cloud. |

The landing rows summarize the original generation briefs; they are not verbatim prompt transcripts.

## MCP illustration: final prompt

Asset: `apps/landing/public/mcp-connection-oil.png`. Generated with the built-in image tool. A portrait composition is preserved on both desktop and phones so the connected records and charts remain visible.

Use case: stylized-concept.
Asset type: a large oil-painted illustration for the MCP feature section of Blaubeere, a calm finance workspace.
Primary request: make MCP's purpose immediately understandable: an AI chat assistant connected through MCP to company financial records, a cash outlook and scenario planning. This must show the actual connection, not a landscape or a vague metaphor.
Scene and subject: a carefully composed, hand-painted technical still life on warm ivory linen. At the top, an open dark sage laptop seen almost straight on, its pale screen showing two unmistakable conversational speech bubbles. Below the laptop, a small brass connector hub with a cream faceplate bearing only the clearly legible letters "MCP". A single visible cable runs from the laptop to this hub, then branches into three visible cables leading to three separate cream financial sheets across the lower half: a ledger with tidy rows of entries, a cash-flow line chart that dips and recovers, and a scenario chart with three distinct diverging lines. The physical connections should be easy to follow. Give each sheet enough breathing room; all three are equally prominent.
Style and medium: sophisticated traditional oil painting on linen, rich tactile brushstrokes, softly scumbled edges, layered cream paint, convincing material and soft natural shadows. Painterly objects and charts, not a vector infographic, glossy 3D render, stock illustration or screenshot.
Composition: portrait 3:4 aspect ratio, complete objects within an 8% safe margin, balanced centered arrangement filling the image. Keep the laptop, connector and all three financial sheets clearly visible and large enough to read at website size. No border, paint edge to edge.
Palette and mood: muted sage, olive, pale blue, warm ivory, small brass accents; calm daylight and the handmade quality of a quiet editorial oil painting.
Text: only "MCP" on the central hub. No other text or numbers, no logos, no people, no robots, no landscapes, no floating sparkles.

## Empty states: final prompts

### `apps/app/public/open-path-oil.png`

Use case: stylized-concept. Asset: original oil-painting illustration for the empty company state in Blaubeere, a calm finance workspace. A simple open wooden gate at the beginning of a narrow path through pale green meadow, the path quietly leading toward distant rolling hills. Wide landscape composition, 3:2 aspect ratio, restrained detail and soft daylight. Traditional oil on linen, tactile layered brushstrokes, gently scumbled pale sky, sage and olive greens, cream and muted blue, consistent with an airy editorial landscape painting. No text, no logos, no interface, no border. Paint the whole image edge to edge.

### `apps/app/public/conversation-oil.png`

Use case: stylized-concept. Asset: original oil-painting illustration for the empty assistant connections state in Blaubeere, a calm finance workspace. Two modest empty wooden chairs side by side overlooking a quiet lake with low green hills across the water, suggesting room for a conversation. Wide landscape composition, 3:2 aspect ratio, chairs in the lower middle, plenty of pale sky and water. Traditional oil on linen, tactile layered brushstrokes, soft diffused daylight, sage and olive greens, cream and muted blue, airy and understated. No people, no text, no logos, no interface, no border. Paint the whole image edge to edge.

The open path belongs to the company-access empty state. The landing’s MCP section uses the dedicated connection illustration above, with a descriptive alternative. Empty-state paintings are decorative; their heading, instructions and action communicate the state without relying on the artwork.

## Garden login artwork

`apps/app/public/garden-gate-oil.png` was generated with the built-in image generation tool. Final prompt:

Use case: stylized-concept. Asset type: original oil painting for one half of a 50/50 finance app login screen, portrait 4:5 composition. Paint a sunlit garden with an open weathered pale wooden gate, a narrow stone path continuing through it into lush grasses, flowering borders, a mature olive tree and soft distant countryside. Gentle afternoon sunlight and dappled shadows. Tactile painterly brush strokes, visible canvas grain, softly blended atmospheric depth, elegant fine-art oil painting, warm cream, sage green, muted olive, touches of dusty blue and butter yellow. Full bleed, no frame, no lettering, no logo, no UI, no watermark. A quiet welcoming scene, believable natural garden, subject beautifully legible at a tall crop. Original image, distinct from a lake or alpine landscape.

A built-in image edit removed an unintended signature: “Remove only the tiny dark signature or lettering at the extreme bottom-right corner of this oil painting. Fill that small area seamlessly with the existing painted stone path and garden ground texture. Keep the entire rest of the image exactly the same: open gate, garden, lighting, brushwork, palette, composition and portrait dimensions. No signature, no watermark, no text anywhere.”

## Garden assistant artwork

`apps/app/public/garden-chairs-oil.png` was generated with the built-in image generation tool. Final prompt:

Use case: stylized-concept. Asset type: original wide oil painting for an app empty-state illustration. Two empty weathered wooden garden chairs beside one another under a leafy tree in a sunlit garden, facing a softly distant meadow. A modest terracotta pot nearby. Pale warm cream and sage green atmosphere, olive foliage, small dusty blue and yellow flowers. Tactile fine-art oil paint with visible brush strokes and canvas texture. Quiet, intimate, welcoming, lots of soft light, gently imperfect. Landscape 3:2 composition, chairs centered in the middle lower half so they remain legible in a wide crop. Full bleed, no text, no logo, no UI, no border or frame, no watermark.

This new garden illustration is used in the assistant empty state. The earlier lake-chair artwork is retained as an original concept. The company empty state continues to use the open-path painting.
