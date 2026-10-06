# User intent: prove captured Taskmaster replies keep their structure (ids, statuses, punctuation, JSON keys) while every
# content word is blanked, so fixtures can be committed without leaking backlog text.
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import IdPseudonyms, redact_reply, redact_text  # noqa: E402


class RedactTest(unittest.TestCase):
    def test_markdown_row_keeps_structure(self):
        src = "- `tm-audit-030` — Rotate vault keys (high, tm-audit, in-review) — Sealed note\n    waiting-on-human: Call Alexandr"
        self.assertEqual(
            redact_text(src),
            "- `tm-audit-030` — xxxxxx xxxxx xxxx (high, xx-xxxxx, in-review) — xxxxxx xxxx\n    waiting-on-human: xxxx xxxxxxxx",
        )

    def test_fields_dates_and_gates_survive(self):
        src = "**gate_state:** review-gate:pass\n**started:** 2026-10-05T09:12\n**Outstanding:** none — ready for done ✓"
        self.assertEqual(redact_text(src), src)

    def test_handover_ids_keep_date_and_task_ids(self):
        self.assertEqual(redact_text("2026-10-05-shipped-big-thing"), "2026-10-05-xxxxxxx-xxx-xxxxx")

    def test_id_shaped_runs_inside_a_slug_are_content(self):
        self.assertEqual(
            redact_text("handovers/2026-06-10-token-diet-shipped-tm-3-16-0-notes.md and `tm-audit-019`/020"),
            "handovers/2026-06-10-xxxxx-xxxx-xxxxxxx-xx-3-16-0-xxxxx.xx and `tm-audit-019`/020",
        )

    def test_superseded_by_and_error_values_are_redacted(self):
        src = json.dumps({"superseded_by": "2026-10-04-audit-fixes-landed", "error": "no backlog at C:\\Users\\someone"})
        out = json.loads(redact_reply(src))
        self.assertEqual(out["superseded_by"], "2026-10-04-xxxxx-xxxxx-xxxxxx")
        self.assertEqual(out["error"], "no xxxxxxx at x:\\xxxxx\\xxxxxxx")

    def test_id_prefixes_are_pseudonymised_consistently(self):
        ids = IdPseudonyms()
        src = ("- `asset-engine-067` — x (critical, sp-audit-0719, in-review)\n"
               "see asset-engine-048, sp-audit-0719-002 and B-082, DEC-026, task/fg-core-002 at 4264e75 on 2026-10-05")
        out = redact_text(src, ids)
        self.assertEqual(out, "- `epic-a-067` — x (critical, epic-b-0719, in-review)\n"
                              "xxx epic-a-048, epic-b-0719-002 and B-082, DEC-026, task/epic-c-002 at 4264e75 on 2026-10-05")
        # The same mapping carries on across replies of one capture.
        self.assertEqual(redact_text("`fg-core-001` then `asset-engine-001`", ids), "`epic-c-001` xxxx `epic-a-001`")
        for real in ("asset", "engine", "audit", "core"):
            self.assertNotIn(real, out)

    def test_json_id_fields_and_branches_are_pseudonymised(self):
        src = json.dumps({"id": "desktop-app-278", "task_id": "desktop-app-278", "task_ids": ["asset-engine-067", "desktop-app-187a"],
                          "branch": "feature/desktop-app-278", "where": "desktop-app-278", "type": "task"})
        out = redact_reply(src, IdPseudonyms())
        self.assertEqual(json.loads(out), {"id": "epic-a-278", "task_id": "epic-a-278", "task_ids": ["epic-b-067", "epic-a-187a"],
                                           "branch": "xxxxxxx/epic-a-278", "where": "epic-a-278", "type": "task"})
        for real in ("desktop", "app-", "asset", "engine"):
            self.assertNotIn(real, out)

    def test_names_run_past_z(self):
        ids = IdPseudonyms()
        names = [ids(f"p{n}-x-001") for n in range(28)]
        self.assertEqual(names[:2] + names[25:], ["epic-a-001", "epic-b-001", "epic-z-001", "epic-aa-001", "epic-ab-001"])

    def test_kept_prefixes_stay(self):
        ids = IdPseudonyms(keep=("zz-missing",))
        self.assertEqual(redact_text("Error: task `zz-missing-999` not found; `fg-core-002`", ids),
                         "Error: task `zz-missing-999` not found; `epic-a-002`")

    def test_json_values_redacted_keys_and_ids_kept(self):
        src = json.dumps({"view": "action", "total": 1, "items": [{"id": "tm-audit-030", "type": "task", "title": "Rotate vault keys",
                                                                    "next": "in-review", "timestamp": "2026-10-05T10:00", "branch": "feat/secret-thing"}]})
        out = json.loads(redact_reply(src))
        item = out["items"][0]
        self.assertEqual(out["view"], "action")
        self.assertEqual(item["id"], "tm-audit-030")
        self.assertEqual(item["title"], "xxxxxx xxxxx xxxx")
        self.assertEqual(item["next"], "in-review")
        self.assertEqual(item["timestamp"], "2026-10-05T10:00")
        self.assertEqual(item["branch"], "xxxx/xxxxxx-xxxxx")

    def test_non_ascii_letters_are_redacted(self):
        # Precomposed, decomposed (e + combining acute), Cyrillic and CJK letters are all content; an id glued to a non-ASCII
        # letter is not an id, so none of its letters survive either.
        src = "Zoë — Café cafe\u0301 Привет 東京 (écrit-12) Straße"
        self.assertEqual(redact_text(src), "xxx — xxxx xxxxx xxxxxx xx (xxxxx-12) xxxxxx")

    def test_paths_and_emails_lose_every_word(self):
        self.assertEqual(redact_text(r"C:\Users\someone\proj and a.b@corp.example"), r"x:\xxxxx\xxxxxxx\xxxx and x.x@xxxx.xxxxxxx")


if __name__ == "__main__":
    unittest.main()
