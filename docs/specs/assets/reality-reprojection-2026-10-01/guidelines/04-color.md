# Color — the concentric model

*Type gives the system its voice. Color gives it atmosphere, signaling and emotional register. Neither is subordinate.*

**Governing trait: complete at every depth.** Most systems start with hue. This one starts with **value** and builds outward. Each ring wraps the one inside it — modulating it, never overruling it — and every depth is a whole system, not a degraded one. A 1-bit display running value alone is color at D1, the way a good black-and-white photograph is not a broken color photo.

## The four dimensions

| | Dimension | Gives | Character | Tokens |
|---|---|---|---|---|
| **D1** | Value | Structure | Calibrated, deliberate steps; off-extremes | `ground-0` … `ground-100`, `foreground-*`, `surface-*`, `border-*` |
| **D2** | Temperature | Identity | Warm by a degree; global per context | The amber bias baked into every ground step |
| **D3** | Hue | Meaning | Earned, restrained | `signature`, `color-*`, `accent-*` |
| **D4** | Saturation | Emphasis within meaning | Surgical; vivid only for maximum intent | `signature-vivid` / `-muted` / `-dim`, `color-*-bold` / `-subtle`, `pastel-*` |

- **D1 Value** does the structural work: foreground/background, surface separation, what to read first, active vs disabled. The resting tones sit inward from the extremes — the darkest dark keeps a trace of air, the lightest light a trace of substance. Steps are deliberate; no accidental mid-tones. Both polarities are native orientations of the same hierarchy.
- **D2 Temperature** tints every value without adding a color. It works below the threshold of attention — people won't think "warm", they'll think "this feels good" or "this is the same system as yesterday." Warm is the system relaxed; cool is the system at attention. Both are authentic, but **temperature is global**: one cool panel in a warm interface reads as broken.
- **D3 Hue** is the first dimension that adds capability — two elements of identical value can now *mean* different things. It works in two territories: **hue as meaning** (success, warning, categories) and **hue as identity** (the signature — the color equivalent of the Declaration, carrying identity *above* the threshold where temperature carries it below). Restraint, not minimalism: color held in reserve so that when it arrives it carries force.
- **D4 Saturation** is the volume knob on hue. The resting state is moderately desaturated so vivid moments land. Full saturation is the Declaration of color — primary action, signature element, critical alert. Low saturation is the Whisper.

## Attributes — what color does

Every color decision is an intersection: a **role** at an **emphasis** in a **state** on a **property**.

- **Roles.** Structural (all depths): *surface*, *foreground*, *accent*. Semantic (D3+, with non-hue fallbacks): *success*, *warning*, *critical*, *informational*, *categorical*. A color without a role is a color without a job. New roles (a "magic" role for AI features, a "new" role) are allowed only with a job no existing role covers and a non-hue fallback.
- **Emphasis.** *Bold* (`foreground-bold`, primary fills), *default* (`foreground-default`), *subtle* (`foreground-subtle`). Emphasis is a value phenomenon first: **if it doesn't read in grayscale, it doesn't work.**
- **States.** Resting, hovered, pressed, focused, disabled — expressed through the lowest dimension that tells them apart. Focus must always differ from hover (`border-focus`).
- **Properties.** *Text* (strictest contrast), *fill* (more chromatic freedom), *ground* (subtle value and temperature), *border* (lower contrast), *icon* (text rules beside text, 3:1 standalone).

## The dimensional map

Read across to the depth the medium supports; everything to the right is unavailable, and the design must still be complete.

| | D1 Value | D2 Temperature | D3 Hue | D4 Saturation |
|---|---|---|---|---|
| **Roles** | Structural only; accent via value contrast | Structural roles gain tonal identity | Semantic roles appear; accent becomes the signature | Urgency within a role |
| **Emphasis** | Full bold → subtle via value | Emphasis gains warmth | Signature at bold, desaturated at subtle | A second emphasis axis |
| **States** | Hover lightens, press deepens, disabled recedes | Tonally consistent shifts | Signature focus ring, critical error state | Pressed more saturated than hovered |
| **Properties** | Text/ground contrast set; fill vs border by value | Ground warmth defines the spatial feel | Fill and border carry semantic hue; text stays value-governed except links and semantic labels | Fill saturation separates primary from secondary |

**D1 → D2** is the subtlest transition: nothing new, everything transformed. **D2 → D3** is the threshold: the question changes from "how much contrast?" to "what does this color *mean*?" **D3 → D4** is where good color becomes considered color.

**The algorithm:** assess depth → identify roles → map emphasis and state → express with available dimensions → **strip the unavailable dimensions and verify.** In this system the strip test is a theme: switch to `survivalist`, where every hue collapses to a ground step, glows go transparent, and the semantic shapes remain.

## Themes

| Theme | Depth | What changes |
|---|---|---|
| `dark` | D1–D4 | The default orientation. Ground 0 is the floor; signature `#5E79E6`. |
| `light` | D1–D4 | Ground inverts 0 ↔ 100. Signature deepens to `#3F58C0`; warning shifts toward orange; shadows warm to `rgba(38,37,35,…)`; frost turns to light translucency. |
| `survivalist` | D1 (+D2) | All hue → value: signature → `ground-100`, semantics → ground steps, accents → ink, glows → transparent. Primary becomes a filled ink block; secondary outlined. The proof. |

## Conduct

- **Neutrality is the default field.** Most of any interface is value and temperature; color means more arriving into a field that isn't already saturated.
- **Color is deployed, not applied.** Not "what color should this be?" but "does this need color, and what job is it doing?"
- **Consistency within, variation between.** Inside a context a hue that means success means it everywhere. Between projects the palette can shift entirely; roles and dimensions remain.
- **Less color, more meaning.** When in doubt, remove one.

## Quiet mischief in color

Mischief lives in the outer dimensions — a brief excursion past the resting state, then back. A loading indicator that flares vivid for a beat; a success that pulses warmer before settling; a hovered element that gains a warmth its resting state lacked; the signature appearing somewhere small and surprising — the selection highlight (already signature at 20%), a scrollbar, a cursor accent. Never random color, decorative gradients or rainbows: a wink, not a costume.

## Non-negotiables

1. The system is complete at every dimensional depth.
2. Value hierarchy functions independently of hue.
3. Color is never the sole carrier of meaning — shapes, labels, position or pattern back every hue.
4. Temperature is consistent within a context.
5. Every hue present has a job.
6. Full saturation is earned.
7. Outer dimensions respect inner ones — hue never overrides value hierarchy; saturation never breaks contrast.

## Legibility ledger

Checked in every theme (WCAG 2): `foreground-bold`, `-default`, `-subtle` pass 4.5:1 on `bg-page`, `surface-ground`, `surface-raised` and `surface-overlay` in all three themes. Known misses, kept exact and flagged:

- `on-signature` on `signature-fill` (dark) 3.5:1 — bold 19px+ labels only; hover fill `signature-fill-hover` 2.3:1.
- `signature-text` on `surface-overlay` 3.8:1 (dark), 3.2:1 (light) — keep signature text off overlays at small sizes.
- `color-critical` as text on dark grounds 3.3–4.3:1; semantic hues as text on light grounds 1.6–4.1:1 — use `color-*-bold` on `surface-ground` and pair with the shape.
- Accents as fills in light polarity take `#0d0d0c` ink; lime and cyan carry no text at all on light ground.
