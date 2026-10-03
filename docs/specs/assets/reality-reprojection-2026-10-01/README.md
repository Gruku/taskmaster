> *The calibration manual for the reprojector.*

Reality Reprojection is a design language and a protocol: a set of beliefs about how designed things should relate to the people who meet them, explicit enough that a human, a codebase or an AI can build from it and land on the same deliberateness. Apply it to a web app, a 128×64 OLED, a poster, a game HUD or a physical object — the outputs look different, but they *think* the same way.

It exists because, without scaffolding, generated work converges on the generic. **Manufactured, not natural. LEGO, not wood.** Every edge is a decision, every radius is a size the factory makes, every motion has a named character.

Read in this order: this page (the rules), then **Core identity** (why), then the primitive you are touching — **Typography**, **Color**, **Space**, **Material**, **Motion** — and **Media & survival** when the surface is constrained. **Open decisions** lists what the system has not settled.

---

## The decision test

Run every element through five questions before it ships. If an answer is no, something changes.

1. **Is every element here because it must be?** If you can remove it and nothing is lost, remove it.
2. **Can the user go deeper if they want to?** The surface is clean; the depth is available on request.
3. **Does it feel like someone cared about making it?** Not expensive. Not decorative. *Cared about.*
4. **Am I working with this medium's nature, or fighting it?** Let its constraints sharpen the expression.
5. **Does it hold its tensions?** Bold *and* detailed. Precise *and* warm. Structured *and* alive.

## Non-negotiables

- **Off-extremes only.** Darkest `ground-0` `#0d0d0c`, lightest `ground-100` `#f5f3ed`. Never `#000`, never `#fff`.
- **Value carries the structure.** Strip every hue: hierarchy, emphasis and state must still read. The `survivalist` theme is the test.
- **Color is never the only signal.** Every semantic color travels with its shape — ● success, ▲ warning, ◆ critical, ⓘ info — or a word.
- **Three voices, never mixed in one element.** Declaration opens, Narrator carries, Technical specifies.
- **No italic, no emoji, whole pixels only.** Emphasis through weight (400 → 600 → 700 → 800).
- **Six manufactured radii:** `radius-xs` 2 · `radius-sm` 3 · `radius-md` 4 · `radius-lg` 6 · `radius-xl` 8 · `radius-full`. No 10px, no 12px, no 50%.
- **Four easing characters only:** `ease-hourglass`, `ease-hourglass-settle`, `ease-pendulum`, `ease-bell`. Never `ease`, `ease-in-out`, `linear` or a raw curve.
- **Buttons lift, tags shift, nothing scales.** `translateY(-1px)` / `translateX(2px)`; never `scale()` on hover.
- **Grain is selective, never global** — and every gradient gets grain.
- **No colored accent borders on cards, no chromatic aberration, no grid overlays on everything.**
- **Every animation has a `prefers-reduced-motion` fallback.** The system works with motion stripped.

---

## Content fundamentals

The system speaks like a well-made manual, not a brochure: quiet confidence, specific over abstract, dry humor in moderation.

- **Address:** declarative second person ("you don't need to know the hex") or systemic imperative ("don't use pure black"). The system believes; it does not hedge — no "we think", rare "we".
- **Specific beats evocative.** Not "warm and inviting" — "`#a8a298`, not `#a0a0a0`. The amber shift, felt not seen."
- **Say no plainly.** "Don't use `scale()` on hover. Ever."
- **Reverent toward craft:** *deliberate, considered, machined, manufactured, etched, surgical, precision, off-extremes, warm bias, polarity* (never "dark mode"), *material, grain, frost, field, emitter, voice, signature, survivalist, dial*.
- **Avoid:** delightful, magical, seamless, intuitive, beautiful, clean and modern, revolutionary, cutting-edge, "crafted with care" (show care; don't claim it).
- **Casing:** Declaration and button labels UPPERCASE (Mode A); body sentence case; section labels and tags UPPERCASE Technical with `tracking-ultra`; tokens `lowercase-with-dashes`.
- **Punctuation:** em dashes for asides (tight in headlines, spaced in body); curly quotes in prose, straight in code. Symbols come from SVG or geometric Unicode (◐ ● ▲ ◆ → ✕), never emoji.

Real lines from the system's own voice:

> Hue is **earned**. It arrives into a context that is already working and adds meaning the lower dimensions could not provide alone. It does not arrive to make things prettier.

> If the system cannot degrade gracefully, it is decoration, not structure.

> A system that cannot evolve is already dead.

---

## Visual foundations

### Color — value first, hue earned

Color is a four-ring stack: **D1 value → D2 temperature → D3 hue → D4 saturation**. Each ring wraps the one inside it, and the system is complete at every depth.

- **Page canvas is `bg-page` (ground-5), not ground-0.** `ground-0` is the floor, reserved for recessed channels (`surface-recessed` inputs and tracks). Cards sit on `surface-raised`, menus and tooltips on `surface-overlay`.
- **Text:** `foreground-bold` for headings and values, `foreground-default` for body, `foreground-subtle` for whisper metadata — all ≥ 4.5:1 on `bg-page`, `surface-raised` and `surface-overlay` in both polarities. `foreground-disabled` is deliberately below 4.5:1: disabled labels only.
- **Temperature is warm, slightly, and global.** Every ground step leans a degree toward amber. Never mix a cool panel into a warm context.
- **Signature is one hue — Periwinkle `signature` `#5E79E6`** (deepens to `#3F58C0` in light polarity). Spend it surgically: primary action (`signature-fill` + `on-signature`), focus ring (`border-focus`), selected state, brand mark, section labels (`signature-text`).
- **Four supporting accents, one job each:** `accent-cyan` coolant/informational, `accent-lime` energy, `accent-pink` warmth/emphasis, `accent-orange` urgency/industrial. As fills they take dark ink; as text only on dark ground at 19px+ bold.
- **Pastels are the intimate dial** (`pastel-*`): long-dwell surfaces and frosted washes, never CTAs or alerts. On light polarity use `pastel-*-grounded`.
- **Semantic:** `color-success`, `color-warning`, `color-critical`, `color-info`, each with `-subtle` (background) and `-bold` (emphasis). In light polarity semantic hues are too light for text — set semantic text in `color-*-bold` on `surface-ground`, and always with the shape.
- **Neutrality is the default field.** When in doubt, remove a color; if the view still communicates, it was not earning its place.

### Polarity — two native states

`dark` and `light` are co-equal; neither is derived. The ground scale inverts (0 ↔ 100), signal hues persist, shadows warm. Set the theme on `<html>` (`data-theme`, and `data-polarity` for source CSS). The polarity toggle is an icon, top-right, never text. The flip animates at `dur-standard` on the Hourglass.

### Typography — three voices

| Voice | Face | Styles | Job |
|---|---|---|---|
| **Declaration** (Mode A) | League Spartan 800 | `declaration-hero` … `declaration-h5` | Claims territory. Uppercase, `tracking-tight`. |
| **Declaration** (Mode B) | Playfair Display 900 | `declaration-alt-hero`, `declaration-alt-h1` | The nature alternate — drop caps, art-deco titles. Replaces Mode A per project. |
| **Narrator** | DM Sans 600 | `narrator-header`, `narrator-body`, `narrator-small`, `narrator-whisper` | Carries content. Generous: 16px/600, `leading-body` 1.6. |
| **Technical** | JetBrains Mono 600 | `technical-metric`, `technical-default`, `technical-small`, `technical-label` | Precision: code, data, labels. |

Section rhythm is fixed: **Technical label → Declaration → Narrator → content.** Energy comes from the distance between voices, not from many weights.

### Space — engineered, then inhabited

- 8px lattice (`space-base`); scale `space-micro` 4 · `xs` 8 · `sm` 12 · `md` 16 · `lg` 24 · `xl` 32 · `2xl` 48 · `3xl` 64.
- **Voices emit space:** after a Declaration leave `emitter-declaration` (96px); Narrator paragraphs keep `emitter-narrator` (24px) rhythm; Technical rows pack at `emitter-technical` (8px).
- Containers: `container-narrow` 720 (reading, forms), `container-max` 1200, `container-wide` 1440 (dashboards), padded by `container-padding`. Framing is always present.
- Proximity is meaning: inside a group tighter than between groups. If removing a divider merges two groups, the spacing was wrong.

### Material — manufactured, not grown

- **Grain encodes rank:** none = baseline, `grain-fine-opacity` = elevated, `grain-coarse-opacity` = premium, `grain-concrete-opacity` = architectural. `grain-gradient-opacity` on every gradient. Never on body, reading surfaces or photographs.
- **Frost** = blur (`frost-blur-light` 8 · `medium` 16 · `heavy` 24) + translucent ground (`frost-bg-*`) + `border-subtle` + shadow. Remove one and it collapses into plastic. Frost for modals, sticky headers, dropdowns — not for reading surfaces.
- **Shadows have two roles:** structural elevation `shadow-sm` → `shadow-md` → `shadow-lg` → `shadow-elevated`, and chromatic identity (`shadow-signature`, `shadow-hover-*`). Light polarity shadows warm to `rgba(38,37,35,…)` and layer richer.
- **The machined edge:** `highlight-inner` on every primary button — light catching an edge. Recessed channels pair `surface-recessed` with `shadow-recessed`.
- Borders: 1px default (`border-default`), 2px for focus and error only.

### Motion — clockwork

- Durations: `dur-micro` 80 (press, snap), `dur-standard` 200 (hover, color), `dur-macro` 400 (panel, modal). Stagger `stagger-base` 50.
- Characters: **Hourglass** flows (hover, reveal), **Hourglass-Settle** overshoots and rests (toggles), **Pendulum** meters (staggers, progress, tab underline), **Bell** arrives (alerts, modals, hero words).
- Motion requires the user: nothing performs while no one is watching. Neutral is a gear.

### Interaction states

Resting → hovered → pressed → focused → disabled, each expressed through the lowest dimension that tells them apart: value shift first, then signature for focus. Focus differs from hover, always (`border-focus` 2px + `signature-glow-strong` halo). Press snaps back in `dur-micro` with `shadow-inset`. Disabled lowers opacity to 0.4 and removes highlights.

### Imagery

The system does not lean on photography. When present: cool-to-neutral cast, high contrast, low saturation (monochrome is safest), framed in `radius-lg` or `radius-xl`, never grained, never full-bleed hero wallpaper.

---

## Iconography

The **bespoke utility pack** (the `Icon` component; SVGs in the Icons group): 16 glyphs, 24px box, 2.25px stroke, round caps and joins, `currentColor`. Stroke, not fill, except where a thing is filled by nature (the arrow's triangle head, dots, the polarity half). The polarity glyph is the identity icon and may carry `signature-text`. For glyphs the pack lacks, draw to the same rules or fall back to Lucide at 2px; never Font Awesome or Material filled sets; never emoji.

## Brand mark

The **raster-plane monogram** (Logos group): a 4×4 field of 2px-cornered cells that step up in presence toward the lower right, where three signature cells land — reprojection as a grid resolving into signal. Pair it with the stacked **REALITY / REPROJECTION** wordmark in League Spartan 800. Use `monogram-dark` / `lockup-dark` on dark grounds and the `-light` pair on light. It must pass the Declaration's self-sufficiency test: the monogram alone, on an empty surface, carries the identity.
