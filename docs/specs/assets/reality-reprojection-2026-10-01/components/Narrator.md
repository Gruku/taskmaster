# Narrator

The reading voice (DM Sans) that carries content and should never make you aware of itself.

**Consumer provides:** `children`, `volume` (`header` · `large` · `default` · `small` · `whisper`), optional `dropcap` (a Mode-B first letter for editorial openings), `as`.

- **Header Narrator** (`narrator-header`, 20px/700) organizes section intros. **Standard** (`narrator-body`, 16px/600) is all body copy. **Whisper** (`narrator-whisper`, 13px, `foreground-subtle`) is captions and metadata — still legible, still considered.
- Weight 600 by default: readability is generous — slightly larger, slightly heavier, `leading-body` 1.6.
- Keep paragraphs on `emitter-narrator` (24px) rhythm; measure 60–75ch; use `container-narrow` for long reading.
- Must pass the **twenty-minute test**. Emphasis through weight (600 → 700), never italic.
- A **Narrator Sibling** (an alternate skin, same skeleton) is defined by intent but not yet cast — do not substitute a font ad hoc.
