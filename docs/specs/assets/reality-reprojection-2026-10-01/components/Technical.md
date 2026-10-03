# Technical

The specialist voice (JetBrains Mono 600) for code, coordinates, readouts and anything where alignment beats flow.

**Consumer provides:** `children`, `size` (`metric` · `default` · `small` · `label`), `as`.

- `technical-metric` (18px, tabular numerals) for live dashboard values — data is primary content, never Narrator-sized.
- `technical-default` for code; `technical-small` for tags, badges, table heads; `technical-label` (11px/800, `tracking-ultra`, uppercase) for labels.
- Must pass the **ambiguity test**: 0/O, 1/l/I, rn/m, `{`/`(` always distinct. Test it with syntax color, not only monochrome.
- Packs dense: `emitter-technical` (8px). Tight framing is its character — but framing is never absent.
