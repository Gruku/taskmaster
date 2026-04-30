// Mirror of taskmaster_v3.snapshot_diff for unsaved-state preview in the client.
// The HTTP endpoint returns the server's version when available; this module is
// used when the client has a draft snapshot in memory and needs to preview the diff.

export function snapshotDiff(a, b) {
  a = a || {}; b = b || {};
  const aTasks = a.tasks || {};
  const bTasks = b.tasks || {};

  const added   = Object.keys(bTasks).filter(k => !(k in aTasks))
                       .map(k => ({ id: k, ...bTasks[k] }));
  const removed = Object.keys(aTasks).filter(k => !(k in bTasks))
                       .map(k => ({ id: k, ...aTasks[k] }));
  const changed = Object.keys(aTasks).filter(k => k in bTasks
                       && JSON.stringify(aTasks[k]) !== JSON.stringify(bTasks[k]))
                       .map(k => ({ id: k, from: aTasks[k], to: bTasks[k] }));

  const aIss = a.issues || {};
  const bIss = b.issues || {};
  const issues_opened = Object.keys(bIss).filter(k => !(k in aIss))
                              .map(k => ({ id: k, ...bIss[k] }));
  const issues_transitioned = Object.keys(aIss).filter(k => k in bIss
                              && (aIss[k].status !== bIss[k].status))
                              .map(k => ({ id: k, from: aIss[k].status, to: bIss[k].status }));

  return {
    tasks_added: added,
    tasks_removed: removed,
    tasks_changed: changed,
    lessons_fired: b.lessons_fired || [],
    issues_opened,
    issues_transitioned,
    files_touched: b.files_touched || [],
  };
}

export default snapshotDiff;
