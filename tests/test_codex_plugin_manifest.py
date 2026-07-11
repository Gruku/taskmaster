import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def test_codex_manifest_exposes_neutral_skills_and_mcp_server():
    manifest = load_json(".codex-plugin/plugin.json")

    assert manifest["name"] == "taskmaster"
    assert manifest["skills"] == "./skills/"
    assert (ROOT / manifest["skills"]).is_dir()

    server = manifest["mcpServers"]["tm"]
    assert server["command"] == "uv"
    assert server["cwd"] == "."
    assert server["args"] == ["run", "backlog_server.py"]
    assert (ROOT / server["args"][-1]).is_file()


def test_distribution_versions_stay_aligned():
    codex = load_json(".codex-plugin/plugin.json")
    claude = load_json(".claude-plugin/plugin.json")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert codex["version"] == "4.4.0"
    assert claude["version"] == codex["version"]
    assert 'version = "4.4.0"' in pyproject
    assert f'## {codex["version"]}' in (ROOT / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )


def test_codex_manifest_describes_the_capability_boundary():
    manifest = load_json(".codex-plugin/plugin.json")
    description = manifest["interface"]["longDescription"]

    assert "assistant-neutral" in description
    assert "enforcement hooks" in description
    assert "intentionally excluded" in description
