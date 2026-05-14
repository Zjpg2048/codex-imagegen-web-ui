"""Startup contract tests for the Codex web UI."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
README_PATH = PROJECT_ROOT / "README.md"
START_WEB_SCRIPT = PROJECT_ROOT / "scripts" / "start_web.sh"


class TestWebStartupScript:
    def test_start_web_script_exists(self):
        assert START_WEB_SCRIPT.is_file()

    def test_start_web_script_is_executable(self):
        mode = START_WEB_SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR

    def test_start_web_script_help_works(self):
        result = subprocess.run(
            ["bash", str(START_WEB_SCRIPT), "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert "Codex imagegen web UI" in result.stdout
        assert "--port" in result.stdout


class TestReadmeWebFlow:
    def test_readme_mentions_codex_web_flow(self):
        content = README_PATH.read_text(encoding="utf-8")

        assert "scripts/start_web.sh" in content
        assert "codex exec" in content
        assert "codex login" in content
