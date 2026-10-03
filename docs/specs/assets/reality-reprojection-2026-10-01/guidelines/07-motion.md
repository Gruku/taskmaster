# Motion — the clockwork

*Without motion the system is a photograph. With it, the system inhabits time.*

**Governing trait: motion is clockwork** — engineered precision you stop to admire. A jeweled bearing is both the mechanism and the detail that catches light. Every motion has a mechanism behind it, even when hidden: a curve tuned by hand, an input-to-output relationship calibrated. The machine has a heartbeat; motion is where you hear it.

## The mechanism

- **The Time Vortex — the substrate.** Motion exists because the user is present; their presence winds the mainspring. A hover is a ripple, a click a puncture, a scroll the lens moving across the canvas, a drag a hand on the gear. How much the system reveals its awareness is a dial — rich on a playful piece, sparing on a dense dashboard. **When the user is still, the system may be still.**
- **The Tick — the pulse.** Cadence without transformation: *when* a screen advances, *when* information appears, how fast a cursor blinks. Every character inherits the Tick; none owns it. A considered Tick feels crafted even with no animation at all — it is motion at D1, and it survives on any display.
- **The Collection — the characters.** How motion flows, below.
- **The Alarm — the interrupt.** Not a member of the Collection: the motion that breaks the rhythm on purpose.

## The Collection

| Character | Nature | Reach for it when | In the system |
|---|---|---|---|
| **The Hourglass** | Continuous flow with mass — things travel and you feel the journey | Hovers, focus, spatial transitions, content repositioning, the polarity flip | `ease-hourglass` at `dur-standard`; modals rise 16px over `dur-macro` |
| **Hourglass-Settle** | The Hourglass's overshoot — a push past the destination, then rest | Discrete positional change | `ease-hourglass-settle` — the toggle thumb |
| **The Pendulum** | Regulated rhythm — arrivals at intervals the eye can trust | Staggered reveals, loading progressions, coordinated multi-element moves | `ease-pendulum`, `stagger-fast`/`-base`/`-slow`; progress sweep, tab underline, skeleton shimmer, tooltip rise, badge pulse |
| **The Flip Clock** | The compound gesture — fluid energy resolving into a mechanical snap within one move (a split-flap card falls, then lands) | Opening and populating; transitioning and landing | Dropdown `scaleY(0→1)` from the top; accordion `0fr → 1fr` reveal; compose Hourglass travel into a Settle landing |
| **The Bell** | The announcement — impact without urgency; the Declaration made temporal | Brand moments, hero words, title arrivals, alerts and dialogs entering | `ease-bell` — alerts drop 8px into place; Declaration entrances |
| **The Digital Clock** | Pure state change — 3 becomes 4, no journey | Toggles, checkboxes, tabs, live data updates | Instant swap; context may cushion it with a `dur-micro` value flash |

Most interfaces lean on one or two characters. The full range is there when the context demands it.

## The Alarm

It works *because* it violates the established cadence — the break in pattern is the signal. It may borrow execution from any character (a shake, a flash, a pulse) and it follows color's escalation: a gentle notice is one soft pulse; a critical fault may shake, flash and persist. Invalid inputs shake once (`input--shake`). Its power comes from rarity, like full saturation.

## The Gear Train

A single interaction may pass through several characters — one energy source, several expressions. This is the most reactive dial in the system: type's mode is set per project, color's temperature per context, but the gear train shifts per interaction, sometimes mid-gesture.

- **Shifts follow energy** — fluid becomes precise at arrival; precision unfurls into flow at departure.
- **Downshifting matters as much as upshifting** — the system must know how to return to calm.
- **Neutral is a gear** — willingness to be still is itself a motion decision.

## Hover grammar

- **Buttons lift** `translateY(-1px)`; press snaps back to `translateY(0)` with `shadow-inset` in `dur-micro`.
- **Tags and nav shift** `translateX(2px)` — a different motion for a different meaning.
- **Cards** earn hover through border and shadow, not transform; `card--tilt` (±2deg perspective) is reserved for signature showcases.
- **Never `scale()` on hover.** `scale(1.02)` is a CSS demo; `translateY(-1px)` is a physical button.

## Quiet mischief in motion

Surprise in time: a barely-visible overshoot, parallax so subtle it reads as depth, a card that tilts toward the cursor, a satisfying gear shift, an error indicator that pulses exactly once. Never random animation, hover effects on everything, or ambient motion running when no one is interacting.

## Reduced motion

Every animation carries a `prefers-reduced-motion` fallback, and each character degrades in its own way: Hourglass → instant; Settle → no overshoot; Pendulum → fade only; Bell → opacity only; shimmers and float-ins off. The Tick survives — order and cadence remain even when transforms don't.

## Non-negotiables

1. Every motion has a reason — what does it say that stillness could not?
2. The Tick persists at every constraint level.
3. Motion respects agency: it never traps, delays or obstructs, and can always be interrupted.
4. Easing is never a default — only the four named curves.
5. Reactivity requires the user; ambient motion must earn its place.
6. Gear shifts are felt, not seen.
7. The Alarm earns its interruption; an overused Alarm is broken.
8. Motion does not fight the medium.
