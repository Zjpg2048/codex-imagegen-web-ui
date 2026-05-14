"""Tests for async generation task flow."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from webapp import (
    GenerationBatchResult,
    GenerationResult,
    GenerationTaskManager,
    render_task_page,
)


def _batch(output_dir: Path) -> GenerationBatchResult:
    return GenerationBatchResult(
        prompt="draw a fox",
        output_dir=output_dir,
        results=[
            GenerationResult(
                prompt="draw a fox",
                image_index=1,
                total_images=2,
                image_path=output_dir / "generated-01.png",
                assistant_message="done-1",
                codex_output="Reading prompt from stdin...\nOpenAI Codex v0.130.0",
            ),
            GenerationResult(
                prompt="draw a fox",
                image_index=2,
                total_images=2,
                image_path=output_dir / "generated-02.png",
                assistant_message="done-2",
                codex_output="tokens used\n17165",
            ),
        ],
    )


class TestGenerationTaskManager:
    def test_start_task_creates_pending_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)

            with patch.object(manager, "_spawn_worker", return_value=None):
                task_id = manager.start_task(prompt="draw a fox", count=2, output_dir=root / "output/web")

            task = manager.get_task(task_id)

        assert task["status"] == "pending"
        assert task["prompt"] == "draw a fox"
        assert task["count"] == 2

    def test_run_task_to_completion_updates_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)
            task_id = manager.create_task_record(prompt="draw a fox", count=2, output_dir=root / "output/web")

            batch = _batch(root / "output/web")
            with patch.object(manager.runner, "run_batch", return_value=batch):
                manager._run_task(task_id)

            task = manager.get_task(task_id)

        assert task["status"] == "completed"
        assert task["result"]["prompt"] == "draw a fox"
        assert len(task["result"]["results"]) == 2

    def test_run_task_updates_partial_results_while_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)
            task_id = manager.create_task_record(prompt="draw a fox", count=2, output_dir=root / "output/web")

            def fake_run_batch(prompt, *, count, output_dir, should_cancel=None, on_result=None, on_output=None):
                if on_result is None:
                    raise AssertionError("on_result callback must be provided")
                on_result(
                    GenerationResult(
                        prompt=prompt,
                        image_index=1,
                        total_images=count,
                        image_path=output_dir / "generated-01.png",
                        assistant_message="done-1",
                        codex_output="OpenAI Codex v0.130.0",
                    )
                )
                running_task = manager.get_task(task_id)
                assert running_task is not None
                assert running_task["status"] == "running"
                assert running_task["completed_count"] == 1
                assert len(running_task["result"]["results"]) == 1
                return _batch(output_dir)

            with patch.object(manager.runner, "run_batch", side_effect=fake_run_batch):
                manager._run_task(task_id)

            task = manager.get_task(task_id)

        assert task["status"] == "completed"
        assert task["completed_count"] == 2
        assert len(task["result"]["results"]) == 2

    def test_run_task_updates_live_log_while_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)
            task_id = manager.create_task_record(prompt="draw a fox", count=1, output_dir=root / "output/web")

            def fake_run_batch(prompt, *, count, output_dir, should_cancel=None, on_result=None, on_output=None):
                if on_output is None:
                    raise AssertionError("on_output callback must be provided")
                on_output("Reading prompt from stdin...\n")
                on_output("OpenAI Codex v0.130.0\n")
                live_task = manager.get_task(task_id)
                assert live_task is not None
                assert "OpenAI Codex v0.130.0" in live_task["live_log"]
                return _batch(output_dir)

            with patch.object(manager.runner, "run_batch", side_effect=fake_run_batch):
                manager._run_task(task_id)

            task = manager.get_task(task_id)

        assert task["status"] == "completed"
        assert "Reading prompt from stdin..." in task["live_log"]
        assert "OpenAI Codex v0.130.0" in task["live_log"]

    def test_run_task_failure_updates_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)
            task_id = manager.create_task_record(prompt="draw a fox", count=2, output_dir=root / "output/web")

            with patch.object(manager.runner, "run_batch", side_effect=RuntimeError("boom")):
                manager._run_task(task_id)

            task = manager.get_task(task_id)

        assert task["status"] == "error"
        assert "boom" in task["error"]


class TestRenderTaskPage:
    def test_pending_page_contains_polling(self):
        html = render_task_page(
            {
                "id": "task-1",
                "status": "pending",
                "prompt": "draw a fox",
                "count": 2,
                "output_dir": "output/web",
                "active_task_count": 1,
            }
        )

        assert "/tasks/task-1/status" in html
        assert "Generating in background" in html
        assert "setTimeout" in html

    def test_running_page_shows_partial_results_and_active_count(self):
        html = render_task_page(
            {
                "id": "task-1",
                "status": "running",
                "prompt": "draw a fox",
                "count": 2,
                "completed_count": 1,
                "active_task_count": 3,
                "output_dir": "output/web",
                "live_log": "Reading prompt from stdin...\nOpenAI Codex v0.130.0\n",
                "result": {
                    "prompt": "draw a fox",
                    "output_dir": "output/web",
                    "results": [
                        {
                            "image_index": 1,
                            "total_images": 2,
                            "image_path": "output/web/generated-01.png",
                            "assistant_message": "done-1",
                            "codex_output": "OpenAI Codex v0.130.0",
                        }
                    ],
                },
            }
        )

        assert "Parallel active tasks: 3" in html
        assert "Completed 1 / 2" in html
        assert 'src="/files/output/web/generated-01.png"' in html
        assert "done-1" in html
        assert "OpenAI Codex v0.130.0" in html
        assert 'id="taskLiveLog"' in html
        assert '"/tasks/task-1/status"' in html

    def test_completed_page_shows_results(self):
        html = render_task_page(
            {
                "id": "task-1",
                "status": "completed",
                "prompt": "draw a fox",
                "count": 2,
                "output_dir": "output/web",
                "result": {
                    "prompt": "draw a fox",
                    "output_dir": "output/web",
                    "results": [
                        {
                            "image_index": 1,
                            "total_images": 2,
                            "image_path": "output/web/generated-01.png",
                            "assistant_message": "done-1",
                            "codex_output": "OpenAI Codex v0.130.0",
                        }
                    ],
                },
            }
        )

        assert "generated-01.png" in html
        assert "done-1" in html
        assert "Export all images" in html
