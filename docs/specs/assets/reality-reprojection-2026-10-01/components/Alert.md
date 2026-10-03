# Alert

A status message on `surface-raised` with its semantic shape icon, a 3px semantic edge, Narrator body and Technical meta; it enters with the Bell (`ease-bell`, `dur-standard`).

**Consumer provides:** `children` (body), `tone` (`success` · `warning` · `critical` · `info`), optional `title`, `meta` (timestamp), `onDismiss`, `toast`.

- The Alarm earns its interruption: match escalation to urgency — a gentle notice is a single soft arrival; a critical fault may persist.
- `critical` gets `role="alert"`; others announce politely.
