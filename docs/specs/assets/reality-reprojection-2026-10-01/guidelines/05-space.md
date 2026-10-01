# Space — framing and composition

*Type gives voice, color gives atmosphere; space gives structure — where everything lives and how the user moves through it.*

**Governing trait: space is engineered — and then inhabited.** Every distance is authored: no defaults, no leftovers, no "that's what the framework gave me." But engineered is not rigid. People zoom to 150%, hide panels, open things side by side. Like a chair built for sitting that still holds when someone perches on the armrest, the structure is designed to survive real use. **Empty space is active, not absent** — a margin has the same status as a voice or a color role: it is doing a job, or it goes.

Two fundamentals: **Framing** (content ↔ its container) and **Composition** (element ↔ element).

## Framing

Framing makes content feel *placed* rather than crammed or lost. It inherits print's vocabulary, which holds for every medium — a phone, a poster, an OLED, a panel floating in AR:

| Term | Meaning | Here |
|---|---|---|
| **Trim** | The container's real edge — a fact of the medium | Viewport, card edge, screen bezel |
| **Safe area** | Where content can reliably live | Notches, gesture zones, curvature |
| **Margin** | Breathing room between content and safe area — the active decision | `container-padding`; card padding `space-md`–`space-lg` |
| **Bleed** | Content deliberately past the trim | Full-bleed bands, a frost backdrop running edge to edge |

Margins follow the voice: the Declaration demands generous framing (the logo on a box face doesn't crowd the edges), the Narrator comfortable framing, the Technical tight framing — a terminal fills its window. Tighter, never absent.

### The HUD and the Canvas

- **The HUD** is framed against the viewport and travels with the viewer: navigation, status, identity, system controls (`Header`, the polarity toggle). It belongs to the viewer, not the content. Its resting state is minimal.
- **The Canvas** is the territory the viewport reveals — **bounded** (a dashboard that fits, a spread) or **unbounded** (a long article, a feed). That is a property of the design, not a responsive behavior.
- **Promotion:** content can migrate from canvas to HUD and back — a pinned reference, the sticky note on the astronaut's helmet. The migration is an explicit act by the user or system.
- **The lens:** the structure exists whether or not the viewport shows it. Whatever portion is visible should feel composed, never arbitrarily cropped.

### Depth in framing

Containers stack — a card in a page, a modal above the page — each with its own margins and safe area. Framing depth is also a color decision: a card sits on `surface-raised` against `bg-page`.

## Composition

- **Position is hierarchy.** The most important element takes the most prominent position; deeper content sits where reaching it takes an action (scroll, open, navigate). **The workbench principle:** things are where they are for a reason — the bolt on the bench is at hand, the bolt in the labeled drawer is findable.
- **Proximity is meaning.** Tight between a label and its input (`space-xs`), moderate between form groups (`space-lg`), generous between sections (`space-2xl`–`space-3xl`). Equal relationships get equal spacing.
- **Grouping and separation.** A group reads as a group because its internal spacing is tighter than its external spacing. Space is the primary separator; rules (`border-subtle`) and value shifts only reinforce it. If removing a divider merges two groups, the spacing was insufficient.
- **Compositional depth.** Elements in different planes of the same container — a dropdown over its trigger, a tooltip over what it annotates. The top layer demands attention (dialog, alert); the resting layer is the working context; collapsed and off-screen content waits below.

## Spatial voices — the emitters

Each voice has a spatial behavior, cast as an emitter token:

| Voice | Behavior | Token | Meaning |
|---|---|---|---|
| Declaration | **Claims territory** — centripetal, draws space toward itself | `emitter-declaration` 96px | *I am separate from what comes next.* |
| Narrator | **Rhythmic** — regular intervals the eye can trust | `emitter-narrator` 24px | *These lines belong to one thought.* |
| Technical | **Efficient** — dense, grid-aligned | `emitter-technical` 8px | *This information is one unit.* |

When voices meet, the change in density *is* the signal that the voice changed.

## Grid and containers

8px lattice, `space-micro` (4) for half-steps inside Technical clusters. `container-narrow` 720 for reading and forms, `container-max` 1200 standard, `container-wide` 1440 for dashboards. Tables are `table-layout: fixed`.

## Quiet mischief in space

**Intentional boundary intrusion:** a pull quote bleeding into the margin, an image breaking the column grid, a notification overlapping a zone it would normally sit beside. It works only because the boundaries are otherwise kept — the wink needs the straight face.

## Non-negotiables

1. Every spatial decision is intentional.
2. Framing is always present — even one pixel on an OLED.
3. Proximity is consistent within a context.
4. Spatial hierarchy works on its own: strip color, flatten type to one weight — the hierarchy still reads.
5. Voices' spatial behaviors are respected: the Declaration gets its territory, the Narrator its rhythm, the Technical its density.
6. Empty space has purpose — unexplained emptiness goes.
7. The structure survives inhabitation — resizing, overflow, customization, assistive zoom.
