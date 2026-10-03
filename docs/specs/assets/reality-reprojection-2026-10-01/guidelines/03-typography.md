# Typography — the voices

*Typography is the most load-bearing element. Before color, before layout, before motion, type is where the identity becomes visible.*

**Governing trait: presence over delicacy.** The system defines *voices* — speaking modes with jobs — and casts fonts into them. A typeface can be replaced; the voice it serves cannot. The model is one speaker at a lectern who can command a room, explain something technical, carry a long story and say something small that makes you lean in. Three instruments that stretch, not five fonts doing five jobs.

## The Declaration

*The name on the building. The title that doesn't ask for attention — it assumes it.*

It exists for moments where type is not conveying information — it *is* the information: a product name on a start-up screen, a section title that anchors a layout, one word on a poster. Heavy, present, authoritative without aggression; the confidence of something machined well. Think of the name that appears for three seconds when a vehicle's system starts.

- **Mode A — Standard** (`declaration-*`, League Spartan 800). Generous proportions, flat squared terminals, geometric underpinning with enough personality not to feel robotic. The needle toward Technical, Confident, Monumental. Select for: a decisive K, a confident R leg, a G that doesn't collapse into softness. Reject: condensed headline faces, rounded terminals, fashion-forward quirks, anything that needs context to work.
- **Mode B — Nature** (`declaration-alt-*`, Playfair Display 900). The illuminated drop cap, the art-deco logotype, the gothic title card — for projects that are more sensorial, human, historically rooted. Drama through form, not just scale; historical lineage visible but never costume. Reject: novelty display faces, scripts, pastiche.
- **The aspiration:** A and B are endpoints of one axis. If variable or parametric type allows it, the ideal Declaration morphs — a project at 40% toward B, where flat terminals soften and geometry picks up a hint of art-deco curve. A north star, not yet a selection criterion.
- **Scale:** at home at display sizes where letters become shapes. At its loudest it fills the viewport (`declaration-hero`); at its quietest it is a three-letter mark anchoring a corner — and still not tentative.

## The Narrator

*The voice that carries you through. The one you spend the most time with. The one that should never make you aware of itself.*

Sustained reading, documentation, descriptions, and the structural roles of interface: labels, headers, pull quotes. Its defining quality is **calm** — not inert, calm; a clear, unhurried speaking voice. Geometrically simple, humanist in its curves: made by hands, not plotted by coordinates. **Slightly thicker than convention** — `narrator-body` is 16px at weight 600 with `leading-body` 1.6 — because readability is generous, not minimal.

It has volume, not separate instruments:

| Volume | Style | Use |
|---|---|---|
| **Header Narrator** | `narrator-header` 20px/700 | Section intros, landmarks. Where the Declaration commands, the Header Narrator *organizes*. |
| **Standard** | `narrator-body` 16px/600 | The workhorse: all body copy. |
| (small) | `narrator-small` 14px/600 | Interface labels, help text. |
| **Whisper** | `narrator-whisper` 13px, `foreground-subtle` | Captions, metadata, timestamps, graph labels. The intimate end — never an afterthought in 9px light gray. |

**The Narrator Sibling** — an alternate skin with the same skeleton (compatible metrics, equivalent tonal range) and a different timbre, for zine-style or personal projects. It *replaces* the Narrator for a piece; it never accompanies it. "A different actor reading the same script." Not yet cast.

## The Technical

*The specialist. The one that speaks in code, coordinates and data.*

Code, terminal output, readouts, tables — anywhere alignment matters more than flow. Monospace by nature, the most legible voice when the measure is individual characters. Clean and modern with considered curves and optical corrections: even at the Technical extreme, the machine has a heartbeat.

- It must coexist with color as a functional layer — a green keyword, an orange string and a gray comment equally legible on both polarities. Evaluate it with a syntax theme, not only monochrome.
- `technical-metric` (18px, tabular numerals) for live values; `technical-default` 14px for code; `technical-small` 12px for tags and table heads; `technical-label` 11px/800 `tracking-ultra` uppercase for labels.
- Home is small-to-medium. Pushing it to display size (a hero code line, a decorative readout) is an occasional treatment.

## How the voices relate

**Contrast is the engine.** If two voices start to feel similar in a layout, something is miscalibrated.

**Hierarchy is shared, identity is not.** The Declaration does not gradually become the Narrator through intermediate weights — the transition is a clean handoff, never a gradient.

| Pairing | Status | Use |
|---|---|---|
| Declaration + Narrator | **Primary** | The Declaration opens, the Narrator continues. |
| Narrator + Technical | Secondary | Narrator gives context, Technical gives specifics — docs, tutorials, data. |
| Declaration + Technical | High-contrast | Dramatic and precise; dials Monumental and Technical at once. Use with intent (mission HUDs). |
| Mode A + Mode B | **Never together** | Alternates, one per project. |
| Narrator + Sibling | **Never together** | Skins, not partners. |

## Non-negotiables

1. **Weight is intentional, never decorative.** Every weight change serves hierarchy or emphasis. No semibold because it's "in between"; no Light because it looks elegant.
2. **Readability is generous.** Slightly larger, slightly more leading, slightly heavier. When in doubt, make it easier to read.
3. **Spacing communicates structure.** Tracked uppercase signals monumental mode; tight leading signals code density. Grammar, not taste.
4. **Type does not fight the medium.** One weight of one voice on an OLED; the Declaration filling 80% of a poster.
5. **The self-sufficiency test** — a Declaration works as 1–3 letters on an empty surface. Otherwise it is only a headline face.
6. **The twenty-minute test** — if the reader notices the Narrator, it fails.
7. **The ambiguity test** — 0/O, 1/l/I, rn/m, `{`/`(`, straight/curly quotes. Failure is a defect, not a style.

Plus the shipped rules: never mix voices within one element; whole-pixel sizes; no italic in the system's own voice.
