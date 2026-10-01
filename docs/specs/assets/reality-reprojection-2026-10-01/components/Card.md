# Card

A framed surface (`surface-raised`, `border-default`, `radius-lg`, `shadow-sm`) that holds one group of related content.

**Consumer provides:** `children` (body), optional `header` (a Declaration h5), `footer` (actions), `variant` (`flat` · `elevated` · `compact` · `spacious` · `interactive` · `signature` · `tilt`, one or an array), `grain` (`fine` · `coarse` · `concrete`).

- **Material is meaning:** no grain = baseline, `fine` = elevated, `coarse` = premium/mission-critical, `concrete` = architectural.
- Hover is earned through border and shadow. `tilt` (±2deg perspective) is a signature effect — project cards and value showcases only.
- Never a coloured left border, never a sliding underline. Differentiate by material, not paint.
- Internal spacing tighter than the gap between cards — that ratio is what makes a group read as a group.
