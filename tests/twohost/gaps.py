# User intent: the table of open gap tasks the harness pins (015 A5 "Pins"); a lane that closes a gap flips its pin and removes its row, so no pin outlives its task.
"""Open gap tasks, id -> what the pin asserts today. Remove a row when its task merges and the pin flips."""
GAPS = {
    "team-server-claim-status": "a member can change the status of a task a teammate holds a claim on (update_task); complete and archive are refused",
    "team-server-ACL": "(Windows) the 014 bundle's DACL grants a principal beyond owner, SYSTEM and Administrators",
}
