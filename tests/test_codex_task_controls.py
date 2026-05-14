"""Tests for task filters and cancellation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from webapp import GenerationTaskManager, render_task_list_page


def test_render_task_list_page_filter_links_and_active_filter():
    html = render_task_list_page(
        [
            {"id": "task-1", "status": "pending", "prompt": "a", "count": 1, "output_dir": "output/web"},
            {"id": "task-2", "status": "completed", "prompt": "b", "count": 1, "output_dir": "output/web"},
        ],
        active_status="pending",
    )

    assert "/tasks?status=all" in html
    assert "/tasks?status=pending" in html
    assert "/tasks?status=completed" in html
    assert "Filter: pending" in html
    assert "task-1" in html
    assert "task-2" not in html


def test_render_task_list_page_cancel_button_for_pending_and_running_only():
    html = render_task_list_page(
        [
            {"id": "task-1", "status": "pending", "prompt": "a", "count": 1, "output_dir": "output/web"},
            {"id": "task-2", "status": "running", "prompt": "b", "count": 1, "output_dir": "output/web"},
            {"id": "task-3", "status": "completed", "prompt": "c", "count": 1, "output_dir": "output/web"},
        ]
    )

    assert '/tasks/task-1/cancel' in html
    assert '/tasks/task-2/cancel' in html
    assert '/tasks/task-3/cancel' not in html


class TestTaskCancellation:
    def test_cancel_pending_task_marks_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = GenerationTaskManager(project_root=Path(tmp))
            task_id = manager.create_task_record(prompt="draw a fox", count=2, output_dir=Path(tmp) / "output/web")

            manager.cancel_task(task_id)
            task = manager.get_task(task_id)

        assert task is not None
        assert task["status"] == "cancelled"
        assert task["error"] == "Cancelled by user"

    def test_cancelled_task_does_not_start_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = GenerationTaskManager(project_root=Path(tmp))
            task_id = manager.create_task_record(prompt="draw a fox", count=2, output_dir=Path(tmp) / "output/web")
            manager.cancel_task(task_id)

            with patch.object(manager.runner, "run_batch") as mock_run_batch:
                manager._run_task(task_id)

            task = manager.get_task(task_id)

        mock_run_batch.assert_not_called()
        assert task is not None
        assert task["status"] == "cancelled"

    def test_running_task_cancel_request_sets_cancel_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = GenerationTaskManager(project_root=Path(tmp))
            task_id = manager.create_task_record(prompt="draw a fox", count=2, output_dir=Path(tmp) / "output/web")
            manager.mark_task_running(task_id)

            manager.cancel_task(task_id)
            task = manager.get_task(task_id)

        assert task is not None
        assert task["status"] == "cancelling"
        assert task["cancel_requested"] is True

    def test_list_tasks_filters_by_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = GenerationTaskManager(project_root=Path(tmp))
            a = manager.create_task_record(prompt="a", count=1, output_dir=Path(tmp) / "out")
            b = manager.create_task_record(prompt="b", count=1, output_dir=Path(tmp) / "out")
            manager.mark_task_running(b)
            manager.cancel_task(a)

            cancelled = manager.list_tasks(status_filter="cancelled")
            running = manager.list_tasks(status_filter="running")

        assert [task["id"] for task in cancelled] == [a]
        assert [task["id"] for task in running] == [b]
