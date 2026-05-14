"""Project startup contract tests."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
README_PATH = PROJECT_ROOT / "README.md"
START_SCRIPT = PROJECT_ROOT / "scripts" / "start.sh"


class TestStartupScript:
    def test_start_script_exists(self):
        assert START_SCRIPT.is_file()

    def test_start_script_is_executable(self):
        mode = START_SCRIPT.stat().st_mode
        assert mode & stat.S_IXUSR

    def test_start_script_help_delegates_to_cli_help(self):
        result = subprocess.run(
            ["bash", str(START_SCRIPT), "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0
        assert "Generate multiple independent images" in result.stdout
        assert "--prompt" in result.stdout
        assert "--count" in result.stdout


class TestReadme:
    def test_readme_exists(self):
        assert README_PATH.is_file()

    def test_readme_mentions_startup_flow(self):
        content = README_PATH.read_text(encoding="utf-8")

        assert "OPENAI_API_KEY" in content
        assert "scripts/start.sh" in content
        assert "python3 generate.py" in content
