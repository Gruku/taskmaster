# Media & survival

*If the system cannot degrade gracefully, it is decoration, not structure.*

The system is meant for web apps, game HUDs, Arduino/OLED displays, posters, physical products, plugins and 3D or AR space. Each medium gets the depth it can render — and every depth is complete.

## Depth per medium

| Medium | Color | Type | Material | Motion |
|---|---|---|---|---|
| **Full web / app** | D1–D4 | All three voices, both Declaration modes | Frost, grain, shadows, radii | Full Collection |
| **Game HUD / diegetic UI** | D1–D4, cool temperature allowed when dialed to Technical | Declaration + Technical (high-contrast pairing) | Frost over the scene, filled/outlined marks | Bell entrances, Digital Clock readouts, Alarm |
| **Color terminal / TUI** | D1–D3, no saturation control | Technical only | Box-drawing edges, filled vs outlined | Tick + Digital Clock |
| **Smartwatch** | D1–D3 | Narrator + Technical | Value steps, minimal borders | Tick, short Hourglass |
| **Monochrome OLED 128×64, e-ink** | D1 only | One weight of one voice | Filled vs outlined, border weight | The Tick alone |
| **Print / poster** | Per run — one-colour is D1+D2 | Declaration may fill 80% of the surface | Real material; bleed is a choice | None — the Tick becomes reading order |
| **AR / spatial** | D1–D4 | Declaration at distance, Narrator near | Frost panels in space | Full, with the HUD fixed to the viewer |

## The survivalist dial

`survivalist` (the theme, or `data-mode="survivalist"` in source CSS) is a continuous dial from full experience to stripped core. In order, it removes: grain → frost (to solid ground) → shadows (simplified) → fonts (to the system stack, `font-survivalist`) → motion (instant) → saturation (toward monochrome). What must survive:

- **Semantic shapes** — ● ▲ ◆ ⓘ. In monochrome they are the only signal left, which is why they travel with every status color everywhere.
- **Value hierarchy** — every structural distinction in ground steps.
- **Filled vs outlined** — primary is a solid ink block, secondary outlined, ghost a hairline.
- **Framing** — even a pixel of margin makes the difference between a well-framed window and a cluttered postage stamp.
- **The Tick** — considered timing with no animation at all.

## The spatial survivalist

When there is no room for margins or depth, two things carry the whole spatial system: **element relations** (large above small, aligned to a shared edge — hierarchy from position and proportion alone) and **navigation through the lens** (the display shows a considered fragment of a larger structure, and physical controls move the lens across it like a blueprint under glass). Structure is not a luxury of large screens.

## Integration gotchas

- **Frost not rendering:** an ancestor has `overflow: hidden`; the frost class sits on an element that isn't `position: relative`; the stylesheet loaded after first paint.
- **Polarity not switching:** the attribute is on `<body>` or a wrapper instead of `<html>`; an element hardcodes a color instead of reading a role token.
- **Canvas and chart drawings stuck after a polarity change:** CSS variables don't reach `fillStyle`/`strokeStyle` — observe the root's `data-theme`/`data-polarity` with a `MutationObserver` and redraw.
- **Dark polarity too dark:** the page uses `ground-0` or pure black as canvas instead of `bg-page`.
