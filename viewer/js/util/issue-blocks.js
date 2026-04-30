// Compute how many tasks an issue blocks: any referenced task with status != 'done'.

export function computeBlocksCount(issue, tasksIndex) {
  const refs = new Set();
  for (const t of issue.related_tasks || []) refs.add(t);
  if (issue.discovered_in_task) refs.add(issue.discovered_in_task);
  if (issue.fixed_in_task) refs.add(issue.fixed_in_task);

  let count = 0;
  for (const id of refs) {
    const task = tasksIndex[id];
    if (!task) continue;
    if (task.status !== 'done') count += 1;
  }
  return count;
}

export default computeBlocksCount;
