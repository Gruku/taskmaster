# Icon

The bespoke single-stroke utility pack: 16 glyphs on a 24px box, 2.25px stroke, round caps and joins, `currentColor` ink.

**Consumer provides:** `name` (`arrow` `chevron` `external` `dismiss` `check` `plus` `minus` `more` `search` `sliders` `edit` `copy` `folder` `document` `grid` `polarity`), optional `size="sm"` (16px), `label` for a meaningful standalone icon (otherwise hidden from assistive tech).

- Stroke, not fill — filled only where the thing is by nature filled (the arrow's triangle head, dots, the polarity half).
- Color follows the text it sits with; standalone icons need 3:1 against their ground.
- Missing glyph? Draw it to the same rules, or fall back to Lucide at 2px. Never emoji.
