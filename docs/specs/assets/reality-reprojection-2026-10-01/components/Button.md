# Button

A manufactured control: it lifts `translateY(-1px)` on hover and snaps back into `shadow-inset` in `dur-micro` (80ms) on press.

**Consumer provides:** `children` (label in Declaration voice, uppercase), `variant` (`primary` · `secondary` · `ghost` · `critical`), `size` (`sm` · `lg`), `icon` for a square icon button (give it `aria-label`), plus native button props.

- One `primary` per view — it spends the signature. Primary carries `highlight-inner`, the machined-edge light.
- Never `scale()` on hover. Never an easing outside the four characters.
- `critical` is for destructive actions only, and its label names the consequence ("Delete 3 files").
- Legibility: dark polarity `on-signature` on `signature-fill` is 3.5:1 — keep primary labels bold and 16px+; hover fill (`signature-fill-hover`) drops to 2.3:1, a known source pair flagged in Open decisions.
- Survivalist: primary becomes a filled ink block, secondary outlined, ghost hairline — filled vs outlined replaces hue.
