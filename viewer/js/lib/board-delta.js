// Pure row delta oracle: a successful apply is one complete board snapshot.
export function applyBoardDelta(board, delta) {
  if (!board || delta.since !== board.cursor || !delta.revision || !delta.cursor ||
      !Array.isArray(delta.tasks_upsert) || !Array.isArray(delta.tasks_remove)) {
    throw new Error('board delta does not match the held snapshot');
  }
  const epics = delta.epics ?? board.epics;
  const phases = delta.phases ?? board.phases;
  const ranks = new Map(epics.map((e, i) => [e.id, i]));
  const byId = new Map(board.tasks.map(t => [t.id, t]));
  for (const t of delta.tasks_upsert) {
    if (!t.id || !ranks.has(t.epic)) throw new Error('invalid board upsert');
    byId.set(t.id, t);
  }
  for (const id of delta.tasks_remove) byId.delete(id);
  const tasks = [...byId.values()].filter(t => ranks.has(t.epic));
  tasks.sort((a, b) => ranks.get(a.epic) - ranks.get(b.epic) || Number(a.order || 0) - Number(b.order || 0) ||
    (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  return {revision: delta.revision, cursor: delta.cursor, tasks, epics, phases};
}
