// b1 wire fields; parity is checked against taskmaster/viewer_dto.py.
export const BOARD_TASK_FIELDS = [
  'id', 'title', 'status', 'priority', 'epic', 'phase', 'area', 'branch',
  'estimate', 'created', 'started', 'completed', 'bundle', 'docs', 'tracker_id',
  'spec_review', 'depends_on', 'sub_repo', 'lane', 'gate_state', 'merge_gate_state',
  'human_action', 'blockers_count', 'archived_reason', 'order',
];
export const BOARD_EPIC_FIELDS = ['id', 'name', 'status', 'last_referenced', 'done_when', 'design_status', 'color'];
export const BOARD_PHASE_FIELDS = ['id', 'name', 'status', 'order'];
