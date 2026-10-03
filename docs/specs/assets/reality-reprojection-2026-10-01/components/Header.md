# Header

The HUD bar: brand (Declaration), navigation and actions, with the polarity toggle at top-right. Optionally frosted and sticky.

**Consumer provides:** `brand`, optional `mark`, `nav` (`{label, href, active}`), `actions`, `frost`, `sticky`, `transparent`, `polarity={false}` to hide the toggle.

- The HUD belongs to the viewer, not the content: keep it minimal. Content may be promoted to it (pinned) and returned.
