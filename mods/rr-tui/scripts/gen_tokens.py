# User intent: turn the Reality Reprojection design system's tokens.json into the colour table rr-tui draws with —
# every token resolved per polarity and every translucent one pre-composited, because a terminal has no alpha.
"""Generate mods/rr-tui/hooks/tokens.ts from the vendored RR tokens.json.

    python mods/rr-tui/scripts/gen_tokens.py            write the table
    python mods/rr-tui/scripts/gen_tokens.py --check    exit 1 when the committed table is stale
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_IN = HERE.parent / "tokens" / "rr-tokens.json"
DEFAULT_OUT = HERE.parent / "hooks" / "tokens.ts"
ARTIFACT = "https://claude.ai/artifact/TAGXgW2cX1PabtPpN3C9ZG"
POLARITIES = ("dark", "light", "survivalist")
GROUNDS = (("page", "bg-page"), ("raised", "surface-raised"), ("overlay", "surface-overlay"))
TONES = ("success", "warning", "critical", "info", "signature")
OFF_EXTREMES = ("#0d0d0c", "#f5f3ed")  # RR non-negotiable: never #000, never #fff
SIGNATURE_TINT_ALPHA = 0.12  # RR has no signature-subtle; match the semantic -subtle strength

_HEX = re.compile(r"^#([0-9a-fA-F]{6})$")
_RGBA = re.compile(r"^rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)$")
_REF = re.compile(r"^\{([a-z0-9-]+)\}$")

Rgba = tuple[int, int, int, float]


def parse_color(text: str) -> Rgba:
    m = _HEX.match(text)
    if m:
        h = m.group(1)
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
    m = _RGBA.match(text)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
    raise ValueError(f"not a colour: {text!r}")


def to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def composite(fg: Rgba, ground_hex: str) -> str:
    gr, gg, gb, _ = parse_color(ground_hex)
    r, g, b, a = fg
    return to_hex(round(r * a + gr * (1 - a)), round(g * a + gg * (1 - a)), round(b * a + gb * (1 - a)))


def luminance(hex_: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b, _ = parse_color(hex_)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def color_values(doc: dict) -> dict[str, object]:
    return {t["name"]: t["value"] for t in doc["color"]["tokens"]}


def resolve(values: dict[str, object], name: str, polarity: str, seen: tuple[str, ...] = ()) -> str:
    if name in seen:
        raise ValueError(f"reference cycle: {' -> '.join((*seen, name))}")
    if name not in values:
        raise KeyError(f"unknown token {name!r}")
    raw = values[name]
    if isinstance(raw, dict):
        raw = raw.get(polarity, raw.get("dark"))
    if not isinstance(raw, str):
        raise ValueError(f"token {name!r} has no value for {polarity}")
    m = _REF.match(raw)
    return resolve(values, m.group(1), polarity, (*seen, name)) if m else raw


def _opaque(values: dict[str, object], name: str, polarity: str) -> str:
    return to_hex(*parse_color(resolve(values, name, polarity))[:3])


def _tone_text(values, tone: str, pol: str) -> str:
    if tone == "signature":
        return _opaque(values, "signature-text", pol)
    return _opaque(values, f"color-{tone}-bold" if pol == "light" else f"color-{tone}", pol)


def _tint_base(values, tone: str, pol: str) -> Rgba:
    if tone == "signature":
        r, g, b, _ = parse_color(resolve(values, "signature", pol))
        return r, g, b, SIGNATURE_TINT_ALPHA
    return parse_color(resolve(values, f"color-{tone}-subtle", pol))


def _tint(base: Rgba, factor: int, ground_hex: str) -> str:
    r, g, b, a = base
    if a >= 1.0:
        return to_hex(r, g, b)
    return composite((r, g, b, min(1.0, a * factor)), ground_hex)


def _keycap_bg(values, tone: str, pol: str) -> str:
    if pol == "survivalist":
        return _opaque(values, "foreground-bold", pol)
    if tone == "signature":
        return _opaque(values, "signature-fill", pol)
    return _opaque(values, f"color-{tone}-bold" if pol == "light" else f"color-{tone}", pol)


def build_table(doc: dict) -> dict[str, dict[str, str]]:
    values = color_values(doc)
    table: dict[str, dict[str, str]] = {}
    for pol in POLARITIES:
        grounds = {g: _opaque(values, token, pol) for g, token in GROUNDS}
        row: dict[str, str] = {}
        for name in values:
            rgba = parse_color(resolve(values, name, pol))
            if rgba[3] >= 1.0:
                row[name] = to_hex(*rgba[:3])
                continue
            row[name] = composite(rgba, grounds["page"])
            row[f"{name}@raised"] = composite(rgba, grounds["raised"])
            row[f"{name}@overlay"] = composite(rgba, grounds["overlay"])
        for tone in TONES:
            row[f"tone.{tone}"] = _tone_text(values, tone, pol)
            base = _tint_base(values, tone, pol)
            for g, ground in grounds.items():
                row[f"tint12.{tone}@{g}"] = _tint(base, 1, ground)
                row[f"tint24.{tone}@{g}"] = _tint(base, 2, ground)
            bg = _keycap_bg(values, tone, pol)
            row[f"keycap.{tone}"] = bg
            row[f"keycap-ink.{tone}"] = max(OFF_EXTREMES, key=lambda ink: contrast(ink, bg))
        table[pol] = dict(sorted(row.items()))
    return table


def render_ts(table: dict[str, dict[str, str]], digest: str) -> str:
    lines = [
        "// User intent: the Reality Reprojection colour table rr-tui draws with — one opaque hex per token and polarity.",
        f"// GENERATED by mods/rr-tui/scripts/gen_tokens.py from mods/rr-tui/tokens/rr-tokens.json (sha256 {digest}).",
        "// Never edit by hand: change the RR artifact, re-vendor tokens.json, rerun the script.",
        "",
        f"export const RR_SOURCE = {{ artifact: '{ARTIFACT}', sha256: '{digest}' }} as const",
        "",
        "export const RR_TABLE: Readonly<Record<'dark' | 'light' | 'survivalist', Readonly<Record<string, string>>>> = {",
    ]
    for pol in POLARITIES:
        lines.append(f"  {pol}: {{")
        lines.extend(f"    '{key}': '{value}'," for key, value in table[pol].items())
        lines.append("  },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="src", default=str(DEFAULT_IN))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--check", action="store_true")
    ns = parser.parse_args(argv)
    raw = Path(ns.src).read_bytes().replace(b"\r\n", b"\n")  # digest must not depend on autocrlf checkout
    text = render_ts(build_table(json.loads(raw.decode("utf-8"))), hashlib.sha256(raw).hexdigest())
    out = Path(ns.out)
    if ns.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            print(f"{out} is stale: rerun gen_tokens.py", file=sys.stderr)
            return 1
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
