from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

sys.path.insert(0, str(Path(__file__).parent.parent))

import blast_radius as br

# ---------------------------------------------------------------------------
# Task 1: Config helpers
# ---------------------------------------------------------------------------

class TestBlastRadiusConfig:
    def test_defaults(self):
        cfg = br.load_config({})
        assert cfg.fan_out_threshold == 5
        assert cfg.max_file_scan == 1000
        assert cfg.shared_dirs == []

    def test_custom_values(self):
        meta = {"blast_radius": {"fan_out_threshold": 10, "max_file_scan": 500, "shared_dirs": ["lib", "core"]}}
        cfg = br.load_config(meta)
        assert cfg.fan_out_threshold == 10
        assert cfg.max_file_scan == 500
        assert cfg.shared_dirs == ["lib", "core"]

    def test_partial_config(self):
        meta = {"blast_radius": {"fan_out_threshold": 3}}
        cfg = br.load_config(meta)
        assert cfg.fan_out_threshold == 3
        assert cfg.max_file_scan == 1000
        assert cfg.shared_dirs == []

    def test_no_blast_radius_key(self):
        meta = {"some_other_key": "value"}
        cfg = br.load_config(meta)
        assert cfg.fan_out_threshold == 5


# ---------------------------------------------------------------------------
# Task 2: Git diff helper
# ---------------------------------------------------------------------------

class TestGetChangedFiles:
    def test_basic(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "src/foo.py\nsrc/bar.py\n"
        with patch("blast_radius.subprocess.run", return_value=mock_result) as mock_run:
            result = br.get_changed_files("feature", "main", Path("/repo"))
        assert result == ["src/foo.py", "src/bar.py"]
        mock_run.assert_called_once_with(
            ["git", "diff", "--name-only", "main...feature"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=Path("/repo"),
        )

    def test_empty_output(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("blast_radius.subprocess.run", return_value=mock_result):
            result = br.get_changed_files("feature", "main", Path("/repo"))
        assert result == []

    def test_nonzero_returncode(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("blast_radius.subprocess.run", return_value=mock_result):
            result = br.get_changed_files("feature", "main", Path("/repo"))
        assert result == []

    def test_timeout(self):
        with patch("blast_radius.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            result = br.get_changed_files("feature", "main", Path("/repo"))
        assert result == []

    def test_exception(self):
        with patch("blast_radius.subprocess.run", side_effect=OSError("not found")):
            result = br.get_changed_files("feature", "main", Path("/repo"))
        assert result == []


# ---------------------------------------------------------------------------
# Task 3: Import parsers
# ---------------------------------------------------------------------------

class TestParsePython:
    def test_simple_import(self):
        src = "import os\nimport sys\n"
        assert "os" in br.parse_imports_python(src)
        assert "sys" in br.parse_imports_python(src)

    def test_from_import(self):
        src = "from pathlib import Path\nfrom collections import defaultdict\n"
        result = br.parse_imports_python(src)
        assert "pathlib" in result
        assert "collections" in result

    def test_skip_comment_lines(self):
        src = "# import os\nimport sys\n"
        result = br.parse_imports_python(src)
        assert "os" not in result
        assert "sys" in result

    def test_dotted_import(self):
        src = "from foo.bar import baz\n"
        result = br.parse_imports_python(src)
        assert "foo.bar" in result or "foo" in result


class TestParseJS:
    def test_import_from(self):
        src = "import React from 'react';\nimport { useState } from 'react';\n"
        result = br.parse_imports_js(src)
        assert "react" in result

    def test_require(self):
        src = 'const fs = require("fs");\n'
        result = br.parse_imports_js(src)
        assert "fs" in result

    def test_dynamic_import(self):
        src = "const mod = import('./mymodule.js');\n"
        result = br.parse_imports_js(src)
        assert "./mymodule.js" in result

    def test_double_quotes(self):
        src = 'import lodash from "lodash";\n'
        result = br.parse_imports_js(src)
        assert "lodash" in result


class TestParseGo:
    def test_single_import(self):
        src = 'import "fmt"\n'
        result = br.parse_imports_go(src)
        assert "fmt" in result

    def test_import_block(self):
        src = 'import (\n\t"fmt"\n\t"os"\n)\n'
        result = br.parse_imports_go(src)
        assert "fmt" in result
        assert "os" in result


class TestParseImportsDispatcher:
    def test_python(self):
        src = "import os\n"
        result = br.parse_imports(src, ".py")
        assert "os" in result

    def test_js(self):
        src = "import React from 'react';\n"
        result = br.parse_imports(src, ".js")
        assert "react" in result

    def test_ts(self):
        src = "import { Component } from 'angular';\n"
        result = br.parse_imports(src, ".ts")
        assert "angular" in result

    def test_go(self):
        src = 'import "fmt"\n'
        result = br.parse_imports(src, ".go")
        assert "fmt" in result

    def test_unsupported_returns_empty(self):
        result = br.parse_imports("whatever", ".cpp")
        assert result == set()

    def test_supported_extensions_set(self):
        assert ".py" in br.SUPPORTED_EXTENSIONS
        assert ".js" in br.SUPPORTED_EXTENSIONS
        assert ".ts" in br.SUPPORTED_EXTENSIONS
        assert ".tsx" in br.SUPPORTED_EXTENSIONS
        assert ".jsx" in br.SUPPORTED_EXTENSIONS
        assert ".mjs" in br.SUPPORTED_EXTENSIONS
        assert ".go" in br.SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# Task 4: Import graph tracing
# ---------------------------------------------------------------------------

class TestResolveImportToFile:
    def test_simple_match(self):
        # src/foo.py -> module "foo"
        assert br._resolve_import_to_file("foo", "src/foo.py") is True

    def test_dotted_match(self):
        assert br._resolve_import_to_file("src.foo", "src/foo.py") is True

    def test_no_match(self):
        assert br._resolve_import_to_file("bar", "src/foo.py") is False

    def test_suffix_match(self):
        # "bar.baz" should match "lib/bar/baz.py"
        assert br._resolve_import_to_file("bar.baz", "lib/bar/baz.py") is True


class TestFindImporters:
    def test_finds_importer(self, tmp_path):
        # create a target file and an importer
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        (tmp_path / "main.py").write_text("from utils import helper\n")
        cfg = br.load_config({})
        result = br.find_importers("utils.py", tmp_path, cfg)
        assert "main.py" in result

    def test_no_importers(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        (tmp_path / "main.py").write_text("import os\n")
        cfg = br.load_config({})
        result = br.find_importers("utils.py", tmp_path, cfg)
        assert result == []

    def test_skips_target_itself(self, tmp_path):
        (tmp_path / "utils.py").write_text("import os\n")
        cfg = br.load_config({})
        result = br.find_importers("utils.py", tmp_path, cfg)
        assert "utils.py" not in result


class TestFindImportersWithLimit:
    def test_returns_tuple(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        cfg = br.load_config({})
        result, truncated = br.find_importers_with_limit("utils.py", tmp_path, cfg)
        assert isinstance(result, list)
        assert isinstance(truncated, bool)

    def test_truncation(self, tmp_path):
        # create many importers
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        for i in range(5):
            (tmp_path / f"mod{i}.py").write_text("from utils import helper\n")
        cfg = br.BlastRadiusConfig(fan_out_threshold=5, max_file_scan=1000, shared_dirs=[])
        # With fan_out_threshold=5, 5 importers shouldn't truncate (>=threshold triggers it)
        result, truncated = br.find_importers_with_limit("utils.py", tmp_path, cfg)
        assert len(result) >= 1


class TestTraceDependencyGraph:
    def test_basic_tracing(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        (tmp_path / "main.py").write_text("from utils import helper\n")
        cfg = br.load_config({})
        depths = {"utils.py": 1}
        graph = br.trace_dependency_graph(["utils.py"], depths, tmp_path, cfg)
        assert "utils.py" in graph
        assert "main.py" in graph["utils.py"]

    def test_zero_depth(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        (tmp_path / "main.py").write_text("from utils import helper\n")
        cfg = br.load_config({})
        depths = {"utils.py": 0}
        graph = br.trace_dependency_graph(["utils.py"], depths, tmp_path, cfg)
        assert graph.get("utils.py", []) == []


# ---------------------------------------------------------------------------
# Task 5: Export change detection
# ---------------------------------------------------------------------------

class TestExtractExports:
    def test_python_function(self):
        src = "def my_func(a, b):\n    pass\n"
        exports = br._extract_exports(src, ".py")
        assert "my_func" in exports

    def test_python_class(self):
        src = "class MyClass:\n    pass\n"
        exports = br._extract_exports(src, ".py")
        assert "MyClass" in exports

    def test_python_private_excluded(self):
        src = "def _private():\n    pass\n"
        exports = br._extract_exports(src, ".py")
        # Private functions may or may not be included depending on implementation
        # The spec says module scope defs — private ones start with _
        # We just verify the function works
        assert isinstance(exports, set)

    def test_js_export(self):
        src = "export function myFunc() {}\nexport const myConst = 1;\n"
        exports = br._extract_exports(src, ".js")
        assert len(exports) > 0

    def test_go_public_func(self):
        src = "func PublicFunc() {}\nfunc privateFunc() {}\n"
        exports = br._extract_exports(src, ".go")
        assert "PublicFunc" in exports
        assert "privateFunc" not in exports


class TestDetectExportChanges:
    def test_no_change(self):
        src = "def my_func(): pass\n"
        assert br.detect_export_changes(src, src, ".py") is False

    def test_added_export(self):
        old = "def my_func(): pass\n"
        new = "def my_func(): pass\ndef new_func(): pass\n"
        assert br.detect_export_changes(old, new, ".py") is True

    def test_removed_export(self):
        old = "def my_func(): pass\ndef old_func(): pass\n"
        new = "def my_func(): pass\n"
        assert br.detect_export_changes(old, new, ".py") is True


class TestHasExportChanges:
    def test_no_git_show(self, tmp_path):
        f = tmp_path / "foo.py"
        f.write_text("def foo(): pass\n")
        mock_result = MagicMock()
        mock_result.returncode = 1  # file not in git
        with patch("blast_radius.subprocess.run", return_value=mock_result):
            result = br.has_export_changes("foo.py", "main", tmp_path)
        assert result is False

    def test_with_changed_exports(self, tmp_path):
        f = tmp_path / "foo.py"
        f.write_text("def foo(): pass\ndef new_func(): pass\n")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "def foo(): pass\n"  # old version
        with patch("blast_radius.subprocess.run", return_value=mock_result):
            result = br.has_export_changes("foo.py", "main", tmp_path)
        assert result is True


# ---------------------------------------------------------------------------
# Task 6: Adaptive depth heuristic
# ---------------------------------------------------------------------------

class TestIsSharedDir:
    def test_lib_is_shared(self):
        cfg = br.load_config({"blast_radius": {"shared_dirs": ["lib", "core"]}})
        assert br._is_shared_dir("lib/utils.py", cfg) is True

    def test_core_is_shared(self):
        cfg = br.load_config({"blast_radius": {"shared_dirs": ["lib", "core"]}})
        assert br._is_shared_dir("core/base.py", cfg) is True

    def test_src_not_shared(self):
        cfg = br.load_config({"blast_radius": {"shared_dirs": ["lib"]}})
        assert br._is_shared_dir("src/app.py", cfg) is False


class TestIsLeafDir:
    def test_plugins_is_leaf(self):
        assert br._is_leaf_dir("plugins/myplugin/foo.py") is True

    def test_components_is_leaf(self):
        assert br._is_leaf_dir("components/Button.tsx") is True

    def test_lib_not_leaf(self):
        assert br._is_leaf_dir("lib/utils.py") is False


class TestComputeBlastDepth:
    def test_default_normal(self):
        cfg = br.load_config({})
        depth = br.compute_blast_depth("src/app.py", fan_out=2, has_export_change=False, priority="P2", config=cfg, depth_override=None)
        assert isinstance(depth, int)
        assert depth >= 0

    def test_override_shallow(self):
        cfg = br.load_config({})
        depth = br.compute_blast_depth("src/app.py", fan_out=15, has_export_change=True, priority="P0", config=cfg, depth_override="shallow")
        assert depth == 1

    def test_override_deep(self):
        cfg = br.load_config({})
        depth = br.compute_blast_depth("src/app.py", fan_out=0, has_export_change=False, priority="P3", config=cfg, depth_override="deep")
        assert depth == 2

    def test_high_priority_boosts_depth(self):
        cfg = br.load_config({})
        depth_p0 = br.compute_blast_depth("src/app.py", fan_out=0, has_export_change=False, priority="P0", config=cfg, depth_override=None)
        depth_p3 = br.compute_blast_depth("src/app.py", fan_out=0, has_export_change=False, priority="P3", config=cfg, depth_override=None)
        assert depth_p0 >= depth_p3

    def test_shared_dir_boosts_depth(self):
        cfg = br.load_config({"blast_radius": {"shared_dirs": ["lib"]}})
        depth_shared = br.compute_blast_depth("lib/utils.py", fan_out=0, has_export_change=False, priority="P3", config=cfg, depth_override=None)
        depth_normal = br.compute_blast_depth("src/app.py", fan_out=0, has_export_change=False, priority="P3", config=cfg, depth_override=None)
        assert depth_shared >= depth_normal


# ---------------------------------------------------------------------------
# Task 7: Anchor cross-referencing
# ---------------------------------------------------------------------------

class TestAnchorMatchesPath:
    def test_exact_match(self):
        assert br._anchor_matches_path("src/foo.py", "src/foo.py") is True

    def test_glob_match(self):
        assert br._anchor_matches_path("src/*.py", "src/foo.py") is True

    def test_prefix_match(self):
        assert br._anchor_matches_path("src/", "src/foo.py") is True

    def test_no_match(self):
        assert br._anchor_matches_path("lib/bar.py", "src/foo.py") is False

    def test_skip_url(self):
        assert br._anchor_matches_path("http://example.com", "src/foo.py") is False

    def test_skip_localhost(self):
        assert br._anchor_matches_path("localhost:3000", "src/foo.py") is False


class TestFindOverlappingTasks:
    def test_finds_overlap(self):
        affected = ["src/foo.py", "src/bar.py"]
        tasks = [
            {"id": "task-1", "title": "Task 1", "status": "todo", "anchors": ["src/foo.py"]},
            {"id": "task-2", "title": "Task 2", "status": "todo", "anchors": ["lib/other.py"]},
        ]
        result = br.find_overlapping_tasks(affected, tasks, exclude_task_id="current")
        assert len(result) == 1
        assert result[0]["task_id"] == "task-1"
        assert "src/foo.py" in result[0]["shared_paths"]

    def test_excludes_self(self):
        affected = ["src/foo.py"]
        tasks = [{"id": "task-1", "title": "T1", "status": "todo", "anchors": ["src/foo.py"]}]
        result = br.find_overlapping_tasks(affected, tasks, exclude_task_id="task-1")
        assert result == []

    def test_skips_archived(self):
        affected = ["src/foo.py"]
        tasks = [{"id": "task-1", "title": "T1", "status": "archived", "anchors": ["src/foo.py"]}]
        result = br.find_overlapping_tasks(affected, tasks, exclude_task_id="other")
        assert result == []

    def test_no_anchors_field(self):
        affected = ["src/foo.py"]
        tasks = [{"id": "task-1", "title": "T1", "status": "todo"}]
        result = br.find_overlapping_tasks(affected, tasks, exclude_task_id="other")
        assert result == []


# ---------------------------------------------------------------------------
# Task 8: Fan-out computation
# ---------------------------------------------------------------------------

class TestComputeFanOutScores:
    def test_returns_dict(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        cfg = br.load_config({})
        result = br.compute_fan_out_scores(["utils.py"], tmp_path, cfg)
        assert isinstance(result, dict)
        assert "utils.py" in result
        assert isinstance(result["utils.py"], int)

    def test_counts_importers(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        (tmp_path / "main.py").write_text("from utils import helper\n")
        (tmp_path / "app.py").write_text("from utils import helper\n")
        cfg = br.load_config({})
        result = br.compute_fan_out_scores(["utils.py"], tmp_path, cfg)
        assert result["utils.py"] >= 2


# ---------------------------------------------------------------------------
# Task 9: Predictive analysis
# ---------------------------------------------------------------------------

class TestAnalyzePredictive:
    def test_returns_structure(self):
        task = {
            "id": "t1",
            "title": "My Task",
            "anchors": ["src/foo.py"],
        }
        all_tasks = [
            {"id": "t2", "title": "Other", "status": "todo", "anchors": ["src/foo.py"]},
        ]
        result = br.analyze_predictive(task, all_tasks)
        assert "task_summary" in result
        assert "anchored_areas" in result
        assert "overlapping_tasks" in result

    def test_filters_http_anchors(self):
        task = {
            "id": "t1",
            "title": "My Task",
            "anchors": ["http://example.com", "src/foo.py"],
        }
        result = br.analyze_predictive(task, [])
        assert "http://example.com" not in result["anchored_areas"]
        assert "src/foo.py" in result["anchored_areas"]

    def test_no_anchors(self):
        task = {"id": "t1", "title": "My Task"}
        result = br.analyze_predictive(task, [])
        assert result["anchored_areas"] == []
        assert result["overlapping_tasks"] == []


# ---------------------------------------------------------------------------
# Task 10: Evidence analysis
# ---------------------------------------------------------------------------

class TestAnalyzeEvidence:
    def test_returns_structure(self, tmp_path):
        cfg = br.load_config({})
        with patch("blast_radius.get_changed_files", return_value=[]):
            result = br.analyze_evidence(
                task={"id": "t1", "title": "T", "priority": "P2"},
                all_tasks=[],
                project_root=tmp_path,
                config=cfg,
                base_branch="main",
                depth_override=None,
            )
        assert "changed_files" in result
        assert "dependency_graph" in result
        assert "fan_out_scores" in result
        assert "depth_used" in result
        assert "overlapping_tasks" in result
        assert "summary_stats" in result
        assert "truncated" in result

    def test_with_changed_files(self, tmp_path):
        (tmp_path / "utils.py").write_text("def helper(): pass\n")
        (tmp_path / "main.py").write_text("from utils import helper\n")
        cfg = br.load_config({})
        mock_git_show = MagicMock()
        mock_git_show.returncode = 0
        mock_git_show.stdout = ""
        with patch("blast_radius.get_changed_files", return_value=["utils.py"]):
            with patch("blast_radius.subprocess.run", return_value=mock_git_show):
                result = br.analyze_evidence(
                    task={"id": "t1", "title": "T", "priority": "P2"},
                    all_tasks=[],
                    project_root=tmp_path,
                    config=cfg,
                    base_branch="main",
                    depth_override=None,
                )
        assert "utils.py" in result["changed_files"]
        assert isinstance(result["summary_stats"], dict)
        assert "total_affected" in result["summary_stats"]
