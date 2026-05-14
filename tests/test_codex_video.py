"""Tests for local Remotion-backed image-to-video generation."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from webapp import (
    GenerationTaskManager,
    DEFAULT_VIDEO_MOTION_PRESET,
    REMOTION_ENTRY_FILE,
    REMOTION_PROJECT_DIR,
    REMOTION_PUBLIC_INPUT_DIR,
    LocalRemotionVideoRunner,
    VideoGenerationResult,
    build_remotion_render_props,
    resolve_video_motion_prompt,
    render_page,
    render_task_page,
    validate_video_motion_preset,
    validate_video_duration,
    validate_video_source_path,
)


def test_validate_video_source_path_accepts_project_image():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        image_path = root / "output" / "web" / "generated-01.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"png")

        resolved = validate_video_source_path(root, "output/web/generated-01.png")

    assert resolved == image_path.resolve()


def test_validate_video_source_path_rejects_non_image_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        text_path = root / "output" / "web" / "note.txt"
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text("x", encoding="utf-8")

        with pytest.raises(ValueError, match="supported image"):
            validate_video_source_path(root, "output/web/note.txt")


def test_validate_video_duration_rejects_out_of_range():
    with pytest.raises(ValueError, match="between"):
        validate_video_duration("1")


def test_validate_video_motion_preset_accepts_default():
    assert validate_video_motion_preset(DEFAULT_VIDEO_MOTION_PRESET) == DEFAULT_VIDEO_MOTION_PRESET


def test_validate_video_motion_preset_rejects_unknown_value():
    with pytest.raises(ValueError, match="video motion preset"):
        validate_video_motion_preset("unknown")


def test_resolve_video_motion_prompt_uses_preset_and_optional_note():
    prompt = resolve_video_motion_prompt("cinematic-push-in", "golden light")
    assert "slow cinematic push-in" in prompt.lower()
    assert "golden light" in prompt


def test_resolve_video_motion_prompt_uses_custom_text_for_custom_preset():
    prompt = resolve_video_motion_prompt("custom", "make the camera jitter slightly")
    assert prompt == "make the camera jitter slightly"


def test_build_remotion_render_props_serializes_expected_fields():
    props = build_remotion_render_props(
        image_file_name="input/test.png",
        motion_prompt="Add a slow cinematic push-in with drifting particles.",
        duration_seconds=4,
        aspect_ratio="16:9",
    )

    assert props["imageFileName"] == "input/test.png"
    assert props["motionPrompt"].startswith("Add a slow cinematic push-in")
    assert props["durationSeconds"] == 4
    assert props["aspectRatio"] == "16:9"


def test_local_remotion_video_runner_builds_npm_render_command():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        runner = LocalRemotionVideoRunner(project_root=root)
        props_file = root / "output" / "videos" / "props.json"
        output_file = root / "output" / "videos" / "video.mp4"

        command = runner.build_command(props_file=props_file, output_file=output_file)

    assert command[:4] == ["npm", "run", "render", "--"]
    assert str((root / REMOTION_PROJECT_DIR / REMOTION_ENTRY_FILE).resolve()) in command
    assert "ImageMotionVideo" in command
    assert str(output_file) in command
    assert any(part == f"--props={props_file}" for part in command)


def test_local_remotion_video_runner_creates_video_result():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_image = root / "output" / "web" / "generated-01.png"
        output_dir = root / "output" / "videos"
        source_image.parent.mkdir(parents=True, exist_ok=True)
        source_image.write_bytes(b"png")
        remotion_install = root / REMOTION_PROJECT_DIR / "node_modules" / "remotion"
        remotion_install.mkdir(parents=True, exist_ok=True)
        (remotion_install / "package.json").write_text("{}", encoding="utf-8")
        runner = LocalRemotionVideoRunner(project_root=root)

        def fake_stream_subprocess_output(command, *, cwd=None, input_text=None, on_output=None):
            assert command[:4] == ["npm", "run", "render", "--"]
            assert cwd == str((root / REMOTION_PROJECT_DIR).resolve())
            props_file = Path(next(part.split("=", 1)[1] for part in command if part.startswith("--props=")))
            props = json.loads(props_file.read_text(encoding="utf-8"))
            assert props["motionPrompt"] == "Animate this into a cinematic shot."
            assert props["durationSeconds"] == 4
            staged_image = root / REMOTION_PROJECT_DIR / "public" / props["imageFileName"]
            assert staged_image.read_bytes() == b"png"
            output_file = Path(command[command.index("ImageMotionVideo") + 1])
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(b"mp4")
            if on_output is not None:
                on_output("rendering\n")
            return "rendering\n"

        with patch("webapp.stream_subprocess_output", side_effect=fake_stream_subprocess_output):
            with patch("webapp.uuid4") as mock_uuid4:
                mock_uuid4.return_value.hex = "generated-01-video"
                result = runner.run(
                    source_image=source_image,
                    motion_prompt="Animate this into a cinematic shot.",
                    output_dir=output_dir,
                    duration_seconds=4,
                    aspect_ratio="16:9",
                )
                assert result.video_path.read_bytes() == b"mp4"

    assert isinstance(result, VideoGenerationResult)
    assert result.video_path.name == "generated-01-video.mp4"


def test_local_remotion_video_runner_requires_local_install():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_image = root / "output" / "web" / "generated-01.png"
        source_image.parent.mkdir(parents=True, exist_ok=True)
        source_image.write_bytes(b"png")
        runner = LocalRemotionVideoRunner(project_root=root)

        with pytest.raises(RuntimeError, match="npm install"):
            runner.run(
                source_image=source_image,
                motion_prompt="Animate this into a cinematic shot.",
                output_dir=root / "output" / "videos",
                duration_seconds=4,
                aspect_ratio="16:9",
            )


def test_render_page_shows_video_generation_form():
    html = render_page()

    assert "Image to video" in html
    assert 'action="/generate-video"' in html
    assert 'name="source_image_path"' in html
    assert 'name="source_image_upload"' in html
    assert 'name="motion_preset"' in html
    assert "Cinematic push-in" in html
    assert 'value="cinematic-push-in"' in html


def test_render_page_marks_selected_video_motion_preset():
    html = render_page(current_video_motion_preset="parallax-float")

    assert 'option value="parallax-float" selected' in html


def test_render_task_page_shows_completed_video_result():
    html = render_task_page(
        {
            "id": "task-video-1",
            "kind": "video",
            "status": "completed",
            "prompt": "Add subtle parallax motion",
            "output_dir": "output/videos",
            "source_image": "output/web/generated-01.png",
            "result": {
                "prompt": "Add subtle parallax motion",
                "output_dir": "output/videos",
                "source_image": "output/web/generated-01.png",
                "video_path": "output/videos/generated-01-video.mp4",
                "assistant_message": "done-video",
                "duration_seconds": 4,
                "aspect_ratio": "16:9",
            },
        }
    )

    assert "generated-01-video.mp4" in html
    assert "<video" in html
    assert "done-video" in html


def test_generation_task_manager_runs_video_task():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_image = root / "output" / "web" / "generated-01.png"
        source_image.parent.mkdir(parents=True, exist_ok=True)
        source_image.write_bytes(b"png")
        manager = GenerationTaskManager(project_root=root)
        task_id = manager.create_task_record(
            prompt="Animate this image",
            count=1,
            output_dir=root / "output" / "videos",
            task_kind="video",
            source_image=source_image,
            duration_seconds=4,
            aspect_ratio="16:9",
        )

        result = VideoGenerationResult(
            prompt="Animate this image",
            source_image=source_image,
            output_dir=root / "output" / "videos",
            video_path=root / "output" / "videos" / "video.mp4",
            assistant_message="done-video",
            duration_seconds=4,
            aspect_ratio="16:9",
        )
        with patch.object(manager.video_runner, "run", return_value=result):
            manager._run_task(task_id)

        task = manager.get_task(task_id)

    assert task is not None
    assert task["status"] == "completed"
    assert task["kind"] == "video"
    assert task["result"]["video_path"].endswith("video.mp4")
