# Material — what things are made of

*Type, color, space and motion kept making promises about depth and surface they couldn't keep alone. Material answers the question they keep asking: what are these things made of?*

**Governing trait: manufactured, not grown.** Surfaces are precision-made — injection-molded matte, machined aluminum, etched glass, a CNC-milled radius. Manufactured is not cold: a LEGO brick is manufactured and playful; aluminum is warm to the touch. The system never simulates nature — no wood grain, leather, paper curl or stone. It references *designed objects*.

**The field is not a void.** The ground is a medium with substance; elements live inside it, inherit its material and override it selectively. That substance comes from color and selective grain — never a blanket texture over the page.

## Properties and their tokens

| Property | Instinct | Carries meaning of | Tokens & classes |
|---|---|---|---|
| **Opacity** | Not fully opaque by default where layering matters | Permanence (permanent → opaque, temporary → translucent), hierarchy, depth | `frost-bg-light` 60% · `-medium` 75% · `-heavy` 85%, `overlay-bg` |
| **Blur** | Frosted, not transparent | Light blur keeps context; heavy blur isolates a surface into its own world | `frost-blur-light` 8 · `-medium` 16 · `-heavy` 24 |
| **Surface finish** | Quality matte, consistent within a context | Rank | `grain-fine` (elevated) · `grain-coarse` (premium) · `grain-concrete` (architectural) · `grain-gradient` (every gradient) · `grain-edge` (felt at borders) |
| **Edge treatment** | Clean, considered | The figure/field boundary | Value shift (cleanest) → `border-default`/`-subtle` 1px → `shadow-*` → space alone; radii `radius-xs` … `radius-xl` |
| **Depth expression** | A combination of the above | Layer order | Higher = more opaque, casts `shadow-md` … `shadow-elevated`; lower = blurred |

**Material is meaning.** A card with coarse grain says "this is serious" before a word is read; a pricing tier's grain density communicates its weight.

## Recipes

- **Frost** = blur + translucent ground + `border-subtle` + shadow. Remove any one and it collapses into plastic. Use for modals, sticky headers, dropdowns, floating panels — never reading surfaces. `backdrop-filter` fails under an ancestor with `overflow: hidden`.
- **Frost-grain** (the gold standard): frost plus an overlay-blended grain at larger scale — the tactile frosted glass.
- **The machined edge:** `highlight-inner` (1px inner top light) on every primary button; `shadow-inner-glow` on active thumbs and filled progress.
- **Milled channel:** `surface-recessed` + `shadow-recessed` for inputs, toggle and progress tracks. The canvas sits above this floor.
- **Gradients** exist only inside elements, never on body or root, and every one carries gradient grain against banding.
- **Light polarity shadows** are warm (`rgba(38,37,35,…)`), lower opacity, layered richer — often the preferred depth.

## Quiet mischief in material

Sensory surprises, immediately understood: a hover that shifts the finish as if polished (`CardGlow`), a depth layer that shifts slightly with the cursor, matte becoming satin when pressed. Not gratuitous blur animation, not glass everywhere, not texture for interest.

## Non-negotiables

1. Material is manufactured, not natural.
2. Translucency is structural — blur and opacity encode permanence, hierarchy, depth; never decoration.
3. Depth is perceivable without color: strip hue and the layering still reads through value, shadow, blur and edge.
4. Edge treatment is deliberate.
5. The ground is not void.
6. Surface finish is consistent within a context — like temperature, a setting, not a per-element whim.
7. Material adapts to the medium: full apparatus on capable displays, filled-vs-outlined on 1-bit.

Shipped refinements: no global grain overlay; no colored accent borders on cards; chromatic aberration retired (it only read at hero scale and fell into the uncanny valley below it); no grid overlays on everything; opacity is for atmosphere, never to mute a solid component — use ground steps.

## The survivalist minimum

On 1-bit, e-ink or a terminal, material reduces to **edge and boundary**: filled vs outlined (solid surface vs bounded area), border weight (heavier = more presence), relative position. It should feel like a good technical drawing — minimal means, every line earning its place. The `survivalist` theme does exactly this: grain off, frost becomes solid `surface-overlay`, shadows off, primary buttons become filled ink, secondary outlined.
