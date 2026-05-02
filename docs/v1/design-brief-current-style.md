# Taskmaster — Current Design Style

> A characterization of the current visual style. Reference, not constraint — the redesign is free to keep, evolve, or replace any of it.

## In one breath

**"Developer-tool dark, semantic-color forward, information-dense, neutral-heavy."** Linear's structural clarity meets GitHub's dark chrome meets a terminal's respect for density.

## In one sentence

> *A calm, dense, semantically-colored developer tool that trusts the user to read small text and uses color exclusively as signal — Linear's restraint with a power-user's tolerance for density.*

---

## The longer characterization

### Aesthetic family

Late-2020s dev tooling — Linear, Height, GitHub Projects, Raycast. Calm dark canvas, accents earned not given, color used as signal not decoration.

### Information density

High. Cards are small but legible; metadata stacks tightly; whitespace is structural, not luxurious. The board assumes a power user who wants to see *everything at once* rather than scroll for it. Closer to a Bloomberg terminal than a Notion page — but softened.

### Color philosophy

*Semantic only.* Every color carries meaning:

- **Red** = critical / blocked
- **Amber** = high / in-review / warnings
- **Blue** = medium / in-progress / interactive accent
- **Green** = active / done
- **Grey** = low / neutral / archived

Nothing is colored "for fun." The result is that color spikes (a red `critical` pill, a glowing card edge) actually *register* as information instead of decoration.

### Hierarchy by tint, not size

Backgrounds layer in subtle steps — `#1a1a1e` page, `#222226` columns, slightly-tinted card headers, mild epic-derived hue washes. The eye separates regions through value contrast, not borders or shadows. Very few hard edges.

### Typography

Inter throughout, 13–15px. Small uppercase tracked labels do the heavy lifting for section dividers. Monospace appears only for things that are *actually code-like* (IDs, branches, paths) — a rule the design follows strictly.

### Personality

Quietly opinionated. The "READ-ONLY · AI Workflow Viewer" pill is a thesis statement — *this isn't a board you drag cards on; it's a window into a system the AI manages.* The dense header, the persistent priority toggles, the keyboard-first interactions, all signal "we expect you to live here."

---

## Distinctive moves worth preserving (or consciously rejecting)

- **Per-project accent hue** — hash-derived from project name. Every project gets a subtle identity color woven through borders and highlights without configuration.
- **Per-epic card tinting** — card headers carry a low-saturation hue from their epic, so you can scan epic ownership across columns without reading labels.
- **Recently-moved glow ring** — accent-colored inset on cards that changed status in the last 2 days. A freshness signal the user doesn't have to opt into.
- **Phase pill train** — numbered steps with connecting lines, doubling as breadcrumb and filter.
- **Semantic-only color discipline** — the entire palette is rationed to meaning. This is the single most defensible thing about the current design.

---

## What it is *not*

- **Not playful or illustrative** — no mascots, no emoji-heavy UI despite the icon picker quirk.
- **Not airy or marketing-styled** — no hero whitespace, no large display type.
- **Not skeuomorphic or material** — flat, no shadows, minimal depth.
- **Not consumer-friendly** — assumes git, branches, monorepos, worktrees as first-class concepts.

---

## How to use this document

This style is the *baseline* the redesign should evaluate against. The team should:

- **Keep** what serves the product: semantic color, density tolerance, calm dark canvas, value-contrast hierarchy, type discipline.
- **Challenge** what doesn't: the structural-lens-only kanban, buried keyboard nav, underpowered settings, the lack of any surface for handovers / lessons / issues / recap / auto-mode.
- **Decide deliberately** on the personality knobs: density level, color saturation, depth/shadow language, whether the "I expect you to live here" stance survives or softens.
