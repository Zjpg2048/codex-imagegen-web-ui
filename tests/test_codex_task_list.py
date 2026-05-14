"""Tests for the task list page."""

from __future__ import annotations

from webapp import render_task_list_page


def test_render_task_list_page_shows_tasks():
    html = render_task_list_page(
        [
            {
                "id": "task-1",
                "status": "pending",
                "prompt": "draw a fox",
                "count": 2,
                "completed_count": 1,
                "output_dir": "output/web",
                "result": {
                    "results": [
                        {
                            "image_path": "output/web/generated-01.png",
                            "assistant_message": "done-a",
                            "image_index": 1,
                            "total_images": 2,
                        }
                    ]
                },
            },
            {
                "id": "task-2",
                "status": "completed",
                "prompt": "draw a whale",
                "count": 1,
                "output_dir": "output/web2",
                "result": {
                    "results": [
                        {
                            "image_path": "output/web2/generated-01.png",
                            "assistant_message": "done-1",
                            "image_index": 1,
                            "total_images": 1,
                        }
                    ]
                },
            },
        ]
    )

    assert "Tasks" in html
    assert "draw a fox" in html
    assert "draw a whale" in html
    assert "/tasks/task-1" in html
    assert "pending" in html
    assert "completed" in html
    assert "Active tasks: 1" in html
    assert "Completed 1 / 2" in html
    assert 'src="/files/output/web/generated-01.png"' in html
    assert "done-a" in html
    assert 'src="/files/output/web2/generated-01.png"' in html
    assert "done-1" in html


def test_render_task_list_page_empty_state():
    html = render_task_list_page([])
    assert "No tasks yet" in html


def test_render_task_list_page_auto_refreshes():
    html = render_task_list_page(
        [
            {
                "id": "task-1",
                "status": "running",
                "prompt": "draw a fox",
                "count": 2,
                "output_dir": "output/web",
            }
        ]
    )
    assert "setTimeout" in html
    assert "window.location.reload" in html
