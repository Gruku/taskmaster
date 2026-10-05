# User intent: pin how RR tokens become terminal colours — references, polarity fallback, alpha compositing, tints and
# keycap ink — so a regenerated table can't silently change what rr-tui draws.
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_tokens as g  # noqa: E402

MINI = {"color": {"tokens": [
    {"name": "ground-0", "value": {"dark": "#0d0d0c", "light": "#f5f3ed"}},
    {"name": "ground-5", "value": {"dark": "#151514", "light": "#e6e4dd"}},
    {"name": "ground-10", "value": {"dark": "#1d1d1b", "light": "#d2cfc8"}},
    {"name": "ground-15", "value": {"dark": "#272725", "light": "#bbb8b1"}},
    {"name": "ground-20", "value": {"dark": "#343331", "light": "#a09c95"}},
    {"name": "ground-100", "value": {"dark": "#f5f3ed", "light": "#0d0d0c"}},
    {"name": "bg-page", "value": "{ground-5}"},
    {"name": "surface-raised", "value": "{ground-10}"},
    {"name": "surface-overlay", "value": "{ground-15}"},
    {"name": "foreground-bold", "value": "{ground-100}"},
    {"name": "signature", "value": {"dark": "#5e79e6", "light": "#3f58c0", "survivalist": "{ground-100}"}},
    {"name": "signature-text", "value": {"dark": "{signature}", "light": "#3f58c0"}},
    {"name": "signature-fill", "value": {"dark": "{signature}", "light": "#3f58c0"}},
    {"name": "signature-dim", "value": {"dark": "rgba(94, 121, 230, 0.24)", "light": "rgba(63, 88, 192, 0.35)", "survivalist": "{ground-15}"}},
    {"name": "color-success", "value": {"dark": "#3a9a5b", "survivalist": "{ground-100}"}},
    {"name": "color-success-subtle", "value": {"dark": "rgba(58, 154, 91, 0.12)", "survivalist": "{ground-15}"}},
    {"name": "color-success-bold", "value": {"dark": "#2d7a47", "survivalist": "{ground-100}"}},
    {"name": "color-warning", "value": {"dark": "#c4881d", "light": "#d4780e", "survivalist": "{ground-100}"}},
    {"name": "color-warning-subtle", "value": {"dark": "rgba(196, 136, 29, 0.12)", "light": "rgba(212, 120, 14, 0.15)", "survivalist": "{ground-15}"}},
    {"name": "color-warning-bold", "value": {"dark": "#a06f14", "light": "#b36100", "survivalist": "{ground-100}"}},
    {"name": "color-critical", "value": {"dark": "#d14343", "survivalist": "{ground-100}"}},
    {"name": "color-critical-subtle", "value": {"dark": "rgba(209, 67, 67, 0.12)", "survivalist": "{ground-15}"}},
    {"name": "color-critical-bold", "value": {"dark": "#b33030", "survivalist": "{ground-100}"}},
    {"name": "color-info", "value": {"dark": "#5b8fc7", "survivalist": "{ground-100}"}},
    {"name": "color-info-subtle", "value": {"dark": "rgba(91, 143, 199, 0.12)", "survivalist": "{ground-15}"}},
    {"name": "color-info-bold", "value": {"dark": "#4574a6", "survivalist": "{ground-100}"}},
]}}
GROUND_VALUES = {"#0d0d0c", "#151514", "#1d1d1b", "#272725", "#343331", "#f5f3ed"}


class ResolveTest(unittest.TestCase):
    def test_missing_polarity_falls_back_to_dark_and_refs_resolve_in_the_same_polarity(self):
        values = g.color_values(MINI)
        self.assertEqual(g.resolve(values, "signature-text", "survivalist"), "#f5f3ed")
        self.assertEqual(g.resolve(values, "signature-text", "light"), "#3f58c0")
        self.assertEqual(g.resolve(values, "bg-page", "light"), "#e6e4dd")

    def test_reference_cycle_raises(self):
        values = {"a": "{b}", "b": "{a}"}
        with self.assertRaises(ValueError):
            g.resolve(values, "a", "dark")

    def test_composite(self):
        self.assertEqual(g.composite((94, 121, 230, 0.24), "#151514"), "#272d46")


class TableTest(unittest.TestCase):
    def setUp(self):
        self.table = g.build_table(MINI)

    def test_every_value_is_opaque_hex(self):
        for pol in g.POLARITIES:
            for key, value in self.table[pol].items():
                self.assertRegex(value, r"^#[0-9a-f]{6}$", f"{pol} {key}")

    def test_translucent_tokens_are_composited_per_ground(self):
        dark = self.table["dark"]
        self.assertEqual(dark["signature-dim"], "#272d46")
        self.assertEqual(dark["signature-dim@overlay"], "#343b53")
        self.assertIn("signature-dim@raised", dark)
        self.assertNotIn("signature@raised", dark)

    def test_tints_are_tone_subtle_at_single_and_double_strength(self):
        dark = self.table["dark"]
        self.assertEqual(dark["tint12.success@page"], "#19251d")
        self.assertEqual(dark["tint24.success@page"], "#1e3525")

    def test_light_semantic_text_uses_bold_variant(self):
        self.assertEqual(self.table["light"]["tone.success"], "#2d7a47")
        self.assertEqual(self.table["light"]["tone.warning"], "#b36100")
        self.assertEqual(self.table["dark"]["tone.warning"], "#c4881d")

    def test_keycap_ink_is_the_higher_contrast_off_extreme(self):
        self.assertEqual(self.table["dark"]["keycap.signature"], "#5e79e6")
        self.assertEqual(self.table["dark"]["keycap-ink.signature"], "#0d0d0c")
        self.assertEqual(self.table["light"]["keycap.signature"], "#3f58c0")
        self.assertEqual(self.table["light"]["keycap-ink.signature"], "#f5f3ed")

    def test_survivalist_signals_carry_value_only(self):
        surv = self.table["survivalist"]
        for key in ("tone.success", "tone.signature", "keycap.critical", "keycap-ink.critical", "foreground-bold"):
            self.assertIn(surv[key], GROUND_VALUES, key)


class CliTest(unittest.TestCase):
    def test_write_then_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "tokens.json"
            out = Path(tmp) / "tokens.ts"
            src.write_text(json.dumps(MINI), encoding="utf-8")
            self.assertEqual(g.main(["--in", str(src), "--out", str(out)]), 0)
            text = out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("// User intent:"))
            self.assertIn("export const RR_TABLE", text)
            self.assertIn("'tone.success': '#3a9a5b',", text)
            self.assertEqual(g.main(["--in", str(src), "--out", str(out), "--check"]), 0)
            out.write_text(text.replace("#3a9a5b", "#000000"), encoding="utf-8")
            self.assertEqual(g.main(["--in", str(src), "--out", str(out), "--check"]), 1)


if __name__ == "__main__":
    unittest.main()
