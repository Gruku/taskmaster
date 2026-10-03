# Open decisions

*Where the original intent and the shipped system disagree, or where intent was defined but never cast. Each needs a call from the system's owner; until then, the shipped rule holds.*

## Intent vs. shipped rule

| # | Original intent | Shipped rule | Proposed resolution |
|---|---|---|---|
| 1 | **Italic** carries personality, warmth and the personal voice; bold carries emphasis. | No italic anywhere. | Keep italic out of the system's own UI voice. Allow it in the **Narrator Sibling** or Mode B editorial pieces as the personal aside — a dial position, not a default. |
| 2 | The Declaration can be upper **or** lowercase; authority comes from weight and scale. | Declaration Mode A is always uppercase. | Uppercase stays the default for Mode A. Permit lowercase for wordmarks and single-word hero moments that pass the self-sufficiency test. Mode B is already sentence case. |
| 3 | The Decision Test has **five** questions (including "working with the medium?" and "does it hold its tensions?"). | Trimmed to three. | Restored to five in this system. |
| 4 | Motion Collection has **five** characters: Hourglass, Pendulum, Flip Clock, Bell, Digital Clock — plus the Alarm, Tick and Gear Train. | Four easing curves (Hourglass, Settle, Pendulum, Bell). | Restored the full vocabulary: Flip Clock = Hourglass travel into a Settle landing; Digital Clock = instant swap with a `dur-micro` cushion. No new curves needed. Consider a dedicated Alarm keyframe set. |
| 5 | Temperature is a dial: **cool is authentic** when dialed toward Technical (mission displays, HUDs). | Only a warm neutral scale exists. | Cast a cool neutral scale as a fourth theme (same 13 steps and luminances, blue-biased) for Technical-dial contexts. |
| 6 | A **Narrator Sibling** — same skeleton, different timbre — for zine-like and personal work. | Not cast. | Cast one against the twenty-minute test and "related, not the same, not strangers." |
| 7 | Additional roles allowed with a job and a fallback — e.g. a **"magic" role for AI features**. | None. | Worth casting for CodeMaestro-style AI surfaces: one hue, one shape fallback. |
| 8 | Inspirations: electric violet + fluorescent orange acid posters, deep forest-green duotone, amber glow. | Periwinkle signature; orange as accent. | Candidates for **moods** (palette seeds between projects), not for the core palette. |
| 9 | The Declaration's A↔B **continuous axis** (variable type morphing geometric → organic). | Two discrete fonts. | North star. Watch for a variable face with a terminal-softness axis. |

## Defects fixed while rebuilding

- **Light-polarity `signature-text` was `#1a6b7a`** — a teal left over from the cyan era. Now `#3F58C0`, matching the light signature (5.6:1 on `surface-ground`).
- **Primary-button hover lost its whole shadow**: it referenced the retired chromatic offset variable, which invalidated the declaration. The chromatic fringe is removed; the hover keeps `highlight-inner` + `shadow-hover-signature`.
- **The generic `.is-active` utility painted open modal overlays and active tab panels in signature.** Containers that are merely open keep `foreground-default`.
- **Survivalist left accents saturated** and put `on-signature` (off-white) on an off-white fill. Accents and pastels now collapse to ground steps and `on-signature` becomes `ground-0` in that theme.
- **Font drift:** the old README named Familjen Grotesk and one stylesheet still loaded it, while the canon and marks use League Spartan. League Spartan is canon everywhere; the fonts ship as files.
- **Hourglass curve drift:** one implementation map listed `cubic-bezier(0.16, 1, 0.3, 1)`; the shipped CSS uses `(0.4, 0, 0.2, 1)`. The shipped curve is canon.

## Known contrast misses (kept, flagged)

See the Legibility ledger in Color. The one worth deciding: in dark polarity, `on-signature` on `signature-fill` is 3.5:1 and on the hover fill 2.3:1. Either keep off-white labels and make primary labels bold 19px+, or switch dark-polarity primary labels to `ground-0` ink (7.6:1 on the hover fill).
