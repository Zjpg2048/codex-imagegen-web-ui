"""Tests for history persistence and explicit export flow."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from webapp import (
    GenerationBatchResult,
    GenerationResult,
    append_history_entry,
    delete_history_image,
    delete_saved_prompts,
    export_generated_file,
    load_history_entries,
    load_saved_prompts,
    render_history_page,
    render_saved_prompts_page,
    save_prompt_entry,
    validate_export_destination,
)


def _sample_batch(output_dir: Path) -> GenerationBatchResult:
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
            ),
            GenerationResult(
                prompt="draw a fox",
                image_index=2,
                total_images=2,
                image_path=output_dir / "generated-02.png",
                assistant_message="done-2",
            ),
        ],
    )


class TestHistoryPersistence:
    def test_append_and_load_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_file = root / "history.json"
            output_dir = root / "output/web"
            output_dir.mkdir(parents=True)
            batch = _sample_batch(output_dir)
            for item in batch.results:
                item.image_path.write_bytes(b"png")

            append_history_entry(history_file, batch)
            entries = load_history_entries(history_file)

        assert len(entries) == 1
        assert entries[0]["prompt"] == "draw a fox"
        assert entries[0]["count"] == 2
        assert entries[0]["output_dir"].endswith("output/web")
        assert len(entries[0]["images"]) == 2
        assert entries[0]["images"][0]["assistant_message"] == "done-1"

    def test_load_history_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = load_history_entries(Path(tmp) / "missing.json")
        assert entries == []

    def test_save_and_load_saved_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved_prompts_file = root / "saved-prompts.json"

            save_prompt_entry(saved_prompts_file, "draw a fox")
            save_prompt_entry(saved_prompts_file, "draw a whale")
            prompts = load_saved_prompts(saved_prompts_file)

        assert len(prompts) == 2
        assert prompts[0]["prompt"] == "draw a whale"
        assert prompts[1]["prompt"] == "draw a fox"

    def test_save_prompt_deduplicates_existing_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved_prompts_file = root / "saved-prompts.json"

            save_prompt_entry(saved_prompts_file, "draw a fox")
            save_prompt_entry(saved_prompts_file, "draw a fox")
            prompts = load_saved_prompts(saved_prompts_file)

        assert len(prompts) == 1
        assert prompts[0]["prompt"] == "draw a fox"

    def test_delete_saved_prompts_supports_multi_select(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved_prompts_file = root / "saved-prompts.json"

            save_prompt_entry(saved_prompts_file, "draw a fox")
            save_prompt_entry(saved_prompts_file, "draw a whale")
            save_prompt_entry(saved_prompts_file, "draw a castle")
            delete_saved_prompts(saved_prompts_file, ["draw a fox", "draw a castle"])
            prompts = load_saved_prompts(saved_prompts_file)

        assert len(prompts) == 1
        assert prompts[0]["prompt"] == "draw a whale"

    def test_delete_history_image_removes_file_and_history_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_file = root / "history.json"
            output_dir = root / "output/web"
            output_dir.mkdir(parents=True)
            batch = _sample_batch(output_dir)
            for item in batch.results:
                item.image_path.write_bytes(b"png")
            append_history_entry(history_file, batch)

            deleted = delete_history_image(history_file, root, output_dir / "generated-01.png")
            entries = load_history_entries(history_file)

        assert deleted["deleted_history_count"] == 1
        assert deleted["deleted_file"] is True
        assert len(entries) == 1
        assert len(entries[0]["images"]) == 1
        assert entries[0]["images"][0]["path"].endswith("generated-02.png")

    def test_delete_history_image_drops_empty_entry_when_last_image_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_file = root / "history.json"
            image_path = root / "output/web/generated-01.png"
            image_path.parent.mkdir(parents=True)
            image_path.write_bytes(b"png")
            history_file.write_text(
                """
[
  {
    "prompt": "draw a fox",
    "count": 1,
    "output_dir": "output/web",
    "created_at": "2026-05-12T10:00:00Z",
    "images": [{"path": "output/web/generated-01.png", "assistant_message": "done-1"}]
  }
]
                """.strip(),
                encoding="utf-8",
            )

            deleted = delete_history_image(history_file, root, image_path)
            entries = load_history_entries(history_file)

        assert deleted["deleted_history_count"] == 1
        assert deleted["deleted_file"] is True
        assert entries == []

    def test_delete_history_image_still_removes_stale_history_when_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history_file = root / "history.json"
            history_file.write_text(
                """
[
  {
    "prompt": "draw a fox",
    "count": 1,
    "output_dir": "output/web",
    "created_at": "2026-05-12T10:00:00Z",
    "images": [{"path": "output/web/generated-01.png", "assistant_message": "done-1"}]
  }
]
                """.strip(),
                encoding="utf-8",
            )

            deleted = delete_history_image(history_file, root, root / "output/web/generated-01.png")
            entries = load_history_entries(history_file)

        assert deleted["deleted_history_count"] == 1
        assert deleted["deleted_file"] is False
        assert entries == []


class TestHistoryRendering:
    def test_render_history_page_shows_entries(self):
        entries = [
            {
                "prompt": "draw a whale",
                "count": 2,
                "output_dir": "output/web",
                "created_at": "2026-05-12T10:00:00Z",
                "images": [
                    {"path": "output/web/generated-01.png", "assistant_message": "done-1"},
                    {"path": "output/web/generated-02.png", "assistant_message": "done-2"},
                ],
            }
        ]

        html = render_history_page(entries)

        assert "draw a whale" in html
        assert "output/web/generated-01.png" in html
        assert "done-2" in html
        assert "History" in html
        assert "Copy prompt" in html
        assert "Use prompt" in html
        assert "Delete file" in html
        assert 'action="/delete-history-image"' in html
        assert 'name="image_path"' in html
        assert "window.confirm" in html
        assert 'data-copy-history-prompt="draw a whale"' in html
        assert 'href="/?prompt=draw%20a%20whale"' in html
        assert "navigator.clipboard.writeText" in html

    def test_render_history_page_shows_toast_and_highlight_for_info_message(self):
        html = render_history_page([], info_message="Deleted 1 history item and removed file")

        assert 'class="toast toast-success"' in html
        assert 'role="status"' in html
        assert "Deleted 1 history item and removed file" in html
        assert 'class="card card-highlight"' in html
        assert "@keyframes toast-in" in html
        assert "@keyframes card-highlight-pulse" in html

    def test_render_saved_prompts_page_shows_saved_prompts(self):
        html = render_saved_prompts_page(
            [
                {"prompt": "draw a fox"},
                {"prompt": "draw a whale"},
            ]
        )

        assert "Saved prompts" in html
        assert "draw a fox" in html
        assert "draw a whale" in html
        assert "Use prompt" in html
        assert 'data-use-saved-prompt="draw a fox"' in html
        assert 'name="saved_prompt"' in html
        assert "Delete selected" in html
        assert 'href="/?prompt=draw%20a%20fox"' in html


class TestExportValidation:
    def test_accepts_existing_absolute_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = validate_export_destination(tmp)
            assert destination.is_dir()

    def test_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            with pytest.raises(ValueError, match="must already exist"):
                validate_export_destination(str(missing))

    def test_rejects_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "file.txt"
            file_path.write_text("x", encoding="utf-8")
            with pytest.raises(ValueError, match="directory"):
                validate_export_destination(str(file_path))


class TestExplicitExport:
    def test_export_generated_file_copies_to_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "output/web/generated-01.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"png-bytes")
            destination_dir = root / "exports"
            destination_dir.mkdir()

            exported = export_generated_file(source, destination_dir)

            assert exported.name == "generated-01.png"
            assert exported.read_bytes() == b"png-bytes"
            assert exported.parent == destination_dir

    def test_export_generated_file_deduplicates_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "output/web/generated-01.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"png-bytes")
            destination_dir = root / "exports"
            destination_dir.mkdir()
            (destination_dir / "generated-01.png").write_bytes(b"old")

            exported = export_generated_file(source, destination_dir)

            assert exported.name.startswith("generated-01-")
            assert exported.suffix == ".png"
            assert exported.read_bytes() == b"png-bytes"
