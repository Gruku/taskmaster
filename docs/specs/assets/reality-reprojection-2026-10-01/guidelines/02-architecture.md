# Architecture & casting

## Four layers

The system is built like a world generator. Each layer builds on the one beneath and may not contradict it.

| Layer | Name | Holds | Test |
|---|---|---|---|
| **L1** | Beliefs | Philosophy, character, tensions, the decision test | When violated, the work feels wrong |
| **L2** | Primitives | Typography (voices), Color (dimensions), Space (field), Material (substance), Motion (clockwork) — their structures and non-negotiables | Durable across media |
| **L3** | World generator | The algorithm (assess medium → set dials → assign roles → apply), moods, token architecture, per-medium rules | Someone who has never seen the work can follow it and produce something that *feels* like Reality Reprojection |
| **L4** | The seed | These exact fonts, hex values, spacing steps and curves | "Is this *the* Reality Reprojection?" — not whether it follows the rules, but whether it is the thing |

This design system is the **L4 seed**: `tokens.json` is the casting, the components are L3 applied, and the guideline sections are L1–L2 carried forward. A different seed — other fonts, another signature — could grow a sibling language from the same L1–L3.

**Signature elements are emergent, not a primitive.** Recognisability is what happens when all five primitives are calibrated together: the monogram's resolving grid, the machined `highlight-inner`, warm off-extremes, Declaration mass against Narrator calm, a Bell-timed arrival. Verify it; don't define it.

## Every primitive has the same skeleton

Each primitive is written the same way, and extensions should be too:

1. **Preamble** — what the primitive gives the system (voice, atmosphere, structure, substance, time).
2. **Cross-primitive trait** — its one governing instinct: Typography *Presence over delicacy* · Color *Complete at every depth* · Space *Space is engineered* · Material *Manufactured, not grown* · Motion *Motion is clockwork*.
3. **Organizing metaphor** — Voices · Dimensions · Framing & Composition · Material properties · The Clockwork.
4. **Quiet mischief** — where play lives and what it is not.
5. **Non-negotiables.**
6. **Survivalist** — what remains on a 1-bit, 128×64 display.
7. **L1 connection** — how each rule traces to a belief.

## The algorithm (L3)

For any new surface:

1. **Assess the medium.** What dimensional depth can it render (D1 value only → D4 full saturation)? What motion (Tick only → full Collection)? What material (filled/outlined → frost and grain)?
2. **Set the dials** — pick or define a mood.
3. **Assign roles** — which voice, which color role, which spatial behavior, which material rank, which motion character each element needs.
4. **Apply** with the tokens available at that depth.
5. **Strip and verify** — remove every unavailable dimension (switch to `survivalist`). If anything stops working, it depended on an outer layer. Restructure.

## Casting ledger

The original primitive documents left every casting table as *TBD* — structure first, selections later. This is what was cast, and why.

### Typography

| Role | Cast | Rationale |
|---|---|---|
| Declaration Mode A | **League Spartan 800** (tokens `font-declaration`, `font-declaration-weight`) | Geometric, flat terminals, heavy without bloat; poster authority; generous proportions, not condensed. Succeeded Syne (fashion-forward) and Familjen Grotesk. |
| Declaration Mode B | **Playfair Display 900** (`font-declaration-alt`) | Warm humanist Didone with ball terminals — passes the drop-cap test; sturdier hairlines than Bodoni Moda, which it replaced. |
| Narrator | **DM Sans 600** (`font-narrator`) | Geometric-simple with humanist curves; calm; the slightly-heavier weight encodes generous readability. |
| Narrator Sibling | *Not yet cast* | See Open decisions. |
| Technical | **JetBrains Mono 600** (`font-technical`) | Passes the ambiguity test; considered curves — the machine has a heartbeat. |

### Color

| Role | Cast | Rationale |
|---|---|---|
| Signature hue | **Periwinkle `#5E79E6`** (`signature`); `#3F58C0` in light | Iris-blueprint blue: distinctly not a framework default, softer and more human than cobalt, reads on warm neutrals. The journey: electric violet → blue-violet → cyan → periwinkle. |
| Neutral scale (warm) | **13 steps `#0d0d0c` → `#f5f3ed`** (`ground-0` … `ground-100`) | Off-extremes, amber-biased by a degree, deliberate gaps — each step has a job. |
| Neutral scale (cool) | *Not yet cast* | See Open decisions. |
| Success | `#3a9a5b` ● | Warm-leaning green, not neon. |
| Warning | `#c4881d` ▲ (`#d4780e` light) | Amber; shifts toward orange on light grounds. |
| Critical | `#d14343` ◆ | The loudest semantic, still below accent saturation. |
| Informational | `#5b8fc7` ⓘ | Steel blue — present, not urgent, kept apart from the signature. |
| Categorical | `accent-cyan`, `accent-lime`, `accent-pink`, `accent-orange` + pastels | The acid-poster instinct (fluorescent against deep ground), held in reserve. |

### Space, material, motion

| Role | Cast |
|---|---|
| Lattice | 8px (`space-base`) |
| Voice emitters | Declaration 96 · Narrator 24 · Technical 8 |
| Radii | 2 · 3 · 4 · 6 · 8 · 9999 — sizes the factory makes |
| Grain | fine .025 · coarse .04 · concrete .03 · gradient .02 · edge .015 |
| Frost | 8 · 16 · 24px blur over 60 · 75 · 85% ground |
| Easing | Hourglass `(0.4,0,0.2,1)` · Settle `(0.34,1.56,0.64,1)` · Pendulum `(0.25,0.1,0.25,1)` · Bell `(0,0,0.2,1)` |
| Durations | 80 · 200 · 400ms; stagger 30 · 50 · 80ms |

## Token architecture

Three tiers: **primitive** values (`ground-20`, `space-md`), **semantic** roles that alias them (`border-default` → `{ground-20}`, `foreground-default` → `{ground-80}`), and **component** tokens that read roles (`signature-fill`, `on-signature`). Themes override only primitives and the few roles that change — aliases re-resolve on their own, which is why light polarity needed no second set of role tokens. Components never read a hex; they read a role.
