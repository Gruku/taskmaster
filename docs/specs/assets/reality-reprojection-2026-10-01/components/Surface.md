# Surface

A material primitive that applies the system's frost, grain, shadow and radius classes to any element.

**Consumer provides:** `children`, any of `frost` (`light` · `medium` · `heavy`), `grain` (`fine` · `coarse` · `concrete` · `gradient` · `edge`), `shadow` (`sm` · `md` · `lg` · `elevated` · `glow` · `inset`), `radius` (`xs`–`xl`, `full`), `as`.

- Every frost = blur + translucent ground + subtle border + shadow. Remove one and it collapses into plastic.
- Translucency is structural: temporary things tend translucent, permanent things opaque. No frost on reading surfaces.
- `backdrop-filter` fails under an ancestor with `overflow: hidden` — keep frost's ancestors unclipped.
- Every gradient gets `grain="gradient"`. Grain never on body, reading surfaces or photography.
