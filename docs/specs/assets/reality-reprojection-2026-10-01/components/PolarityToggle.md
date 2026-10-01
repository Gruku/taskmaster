# PolarityToggle

The icon-only control that flips polarity by setting `data-theme` and `data-polarity` on `<html>`; the switch animates at `dur-standard` on the Hourglass like a physical flip.

**Consumer provides:** optional `onChange(next)`.

- Always an icon (the polarity glyph), never "Dark / Light" text. Position: top-right.
- Polarity attributes belong on `<html>`, never `<body>`. Canvas charts must redraw on change.
