"""Tests for the Codex-backed image generation web UI."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from webapp import (
    CodexImageGenRunner,
    CodexImageGenHandler,
    ImageAnalysisResult,
    GenerationBatchResult,
    GenerationResult,
    GenerationTaskManager,
    append_history_entry,
    build_codex_exec_prompt,
    build_codex_image_analysis_prompt,
    render_page,
    resolve_output_dir,
    save_reference_images,
    sanitize_upload_filename,
    validate_image_analysis_mode,
    validate_count,
    validate_prompt,
)


class TestValidatePrompt:
    def test_rejects_blank_prompt(self):
        with pytest.raises(ValueError, match="must not be empty"):
            validate_prompt("   ")

    def test_rejects_overlong_prompt(self):
        with pytest.raises(ValueError, match="at most"):
            validate_prompt("x" * 2001)

    def test_trims_valid_prompt(self):
        assert validate_prompt("  hello  ") == "hello"


class TestValidateCount:
    def test_accepts_valid_count(self):
        assert validate_count("3") == 3

    def test_rejects_non_integer(self):
        with pytest.raises(ValueError, match="integer"):
            validate_count("x")

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError, match="between"):
            validate_count("0")


class TestResolveOutputDir:
    def test_uses_default_when_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = resolve_output_dir(root, "")
        assert result == root / "output/web"

    def test_allows_project_relative_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = resolve_output_dir(root, "renders/final")
        assert result == root / "renders/final"

    def test_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with pytest.raises(ValueError, match="inside the project"):
                resolve_output_dir(root, "../outside")

    def test_rejects_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with pytest.raises(ValueError, match="relative"):
                resolve_output_dir(root, "/tmp/outside")


class TestBuildCodexExecPrompt:
    def test_embeds_user_prompt_and_imagegen_instruction(self):
        prompt = build_codex_exec_prompt("a red fox in snowfall", image_index=2, total_images=4)

        assert "imagegen" in prompt.lower()
        assert "Generate exactly one image" in prompt
        assert "image 2 of 4" in prompt
        assert "a red fox in snowfall" in prompt

    def test_mentions_reference_images_when_present(self):
        prompt = build_codex_exec_prompt(
            "make a new pose",
            image_index=1,
            total_images=2,
            has_reference_images=True,
        )

        assert "reference image" in prompt.lower()
        assert "make a new pose" in prompt

    def test_builds_reverse_prompt_prompt(self):
        prompt = build_codex_image_analysis_prompt("reverse-prompt", "focus on style and outfit")

        assert "prompt engineer" in prompt.lower()
        assert "focus on style and outfit" in prompt
        assert "Do not generate an image" in prompt

    def test_builds_structured_analysis_prompt(self):
        prompt = build_codex_image_analysis_prompt("structured-analysis", "focus on style and outfit")

        assert "json object" in prompt.lower()
        assert "style" in prompt.lower()
        assert "focus on style and outfit" in prompt


class TestImageAnalysisMode:
    def test_accepts_supported_modes(self):
        assert validate_image_analysis_mode("reverse-prompt") == "reverse-prompt"
        assert validate_image_analysis_mode("structured-analysis") == "structured-analysis"

    def test_rejects_unknown_mode(self):
        with pytest.raises(ValueError, match="image analysis mode"):
            validate_image_analysis_mode("caption")


class TestCodexImageGenRunner:
    def test_builds_codex_exec_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = CodexImageGenRunner(
                project_root=root,
                codex_generated_root=root / "codex-generated",
                default_output_root=root / "app-output",
            )

            command = runner.build_command("draw a robot", image_index=1, total_images=1)

        assert command[:3] == ["codex", "exec", "--skip-git-repo-check"]
        assert "--output-last-message" in command
        assert not any("draw a robot" in part for part in command)

    def test_builds_codex_exec_command_with_reference_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_one = root / "uploads" / "one.png"
            image_two = root / "uploads" / "two.jpg"
            image_one.parent.mkdir(parents=True, exist_ok=True)
            image_one.write_bytes(b"1")
            image_two.write_bytes(b"2")
            runner = CodexImageGenRunner(
                project_root=root,
                codex_generated_root=root / "codex-generated",
                default_output_root=root / "app-output",
            )

            command = runner.build_command(
                "draw a robot",
                image_index=1,
                total_images=1,
                reference_images=[image_one, image_two],
            )

        image_flags = [command[i + 1] for i, part in enumerate(command) if part == "--image"]
        assert str(image_one) in image_flags
        assert str(image_two) in image_flags

    def test_run_copies_newly_generated_images_and_reads_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated_root = root / "codex-generated"
            output_root = root / "app-output"
            runner = CodexImageGenRunner(
                project_root=root,
                codex_generated_root=generated_root,
                default_output_root=output_root,
            )

            created_count = 0

            def fake_stream_subprocess_output(command, *, cwd=None, input_text=None, on_output=None):
                nonlocal created_count
                created_count += 1
                assert input_text is not None
                assert "draw a cat" in input_text
                message_path = Path(command[command.index("--output-last-message") + 1])
                message_path.write_text(f"done-{created_count}", encoding="utf-8")
                session_dir = generated_root / f"session-{created_count}"
                session_dir.mkdir(parents=True, exist_ok=True)
                created = session_dir / f"ig_test_{created_count}.png"
                created.write_bytes(f"png-{created_count}".encode("utf-8"))
                output = f"Reading prompt from stdin...\nOpenAI Codex v0.130.0\ncodex\ndone-{created_count}\n\ntokens used\n1700{created_count}"
                if on_output is not None:
                    on_output(output)
                return output

            with patch("webapp.stream_subprocess_output", side_effect=fake_stream_subprocess_output):
                batch = runner.run_batch("draw a cat", count=2, output_dir=output_root)

            assert isinstance(batch, GenerationBatchResult)
            assert batch.prompt == "draw a cat"
            assert len(batch.results) == 2
            assert [item.assistant_message for item in batch.results] == ["done-1", "done-2"]
            assert "OpenAI Codex v0.130.0" in batch.results[0].codex_output
            assert "tokens used" in batch.results[0].codex_output
            assert [item.image_index for item in batch.results] == [1, 2]
            assert all(item.image_path.is_file() for item in batch.results)
            assert [item.image_path.read_bytes() for item in batch.results] == [b"png-1", b"png-2"]
            assert all(item.image_path.parent.resolve() == output_root.resolve() for item in batch.results)

    def test_run_errors_when_no_new_image_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = CodexImageGenRunner(
                project_root=root,
                codex_generated_root=root / "codex-generated",
                default_output_root=root / "app-output",
            )

            def fake_stream_subprocess_output(command, *, cwd=None, input_text=None, on_output=None):
                assert input_text is not None
                message_path = Path(command[command.index("--output-last-message") + 1])
                message_path.write_text("done", encoding="utf-8")
                return "done"

            with patch("webapp.stream_subprocess_output", side_effect=fake_stream_subprocess_output):
                with pytest.raises(RuntimeError, match="No new image"):
                    runner.run_batch("draw a cat", count=1, output_dir=root / "app-output")

    def test_run_with_reference_images_sends_prompt_via_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated_root = root / "codex-generated"
            output_root = root / "app-output"
            reference_image = root / "uploads" / "one.png"
            reference_image.parent.mkdir(parents=True, exist_ok=True)
            reference_image.write_bytes(b"1")
            runner = CodexImageGenRunner(
                project_root=root,
                codex_generated_root=generated_root,
                default_output_root=output_root,
            )

            def fake_stream_subprocess_output(command, *, cwd=None, input_text=None, on_output=None):
                assert "--image" in command
                assert str(reference_image) in command
                assert input_text is not None
                assert "reference image" in input_text.lower()
                assert "draw a robot" in input_text
                message_path = Path(command[command.index("--output-last-message") + 1])
                message_path.write_text("done", encoding="utf-8")
                session_dir = generated_root / "session-1"
                session_dir.mkdir(parents=True, exist_ok=True)
                created = session_dir / "ig_test_1.png"
                created.write_bytes(b"png-1")
                return "done"

            with patch("webapp.stream_subprocess_output", side_effect=fake_stream_subprocess_output):
                batch = runner.run_batch(
                    "draw a robot",
                    count=1,
                    output_dir=output_root,
                    reference_images=[reference_image],
                )

            assert len(batch.results) == 1

    def test_run_streams_terminal_output_to_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated_root = root / "codex-generated"
            output_root = root / "app-output"
            runner = CodexImageGenRunner(
                project_root=root,
                codex_generated_root=generated_root,
                default_output_root=output_root,
            )
            streamed_chunks: list[str] = []

            class FakePopen:
                def __init__(self, *args, **kwargs):
                    self.stdin = self
                    self.stdout = iter(["Reading prompt from stdin...\n", "OpenAI Codex v0.130.0\n"])
                    self.returncode = 0

                def write(self, data: str) -> None:
                    assert "draw a cat" in data

                def close(self) -> None:
                    return

                def wait(self) -> int:
                    message_path = output_root / ".last-codex-message.txt"
                    message_path.parent.mkdir(parents=True, exist_ok=True)
                    message_path.write_text("done", encoding="utf-8")
                    session_dir = generated_root / "session-1"
                    session_dir.mkdir(parents=True, exist_ok=True)
                    created = session_dir / "ig_test_1.png"
                    created.write_bytes(b"png-1")
                    return self.returncode

            with patch("webapp.subprocess.Popen", return_value=FakePopen()):
                batch = runner.run_batch(
                    "draw a cat",
                    count=1,
                    output_dir=output_root,
                    on_output=streamed_chunks.append,
                )

            assert streamed_chunks == ["Reading prompt from stdin...\n", "OpenAI Codex v0.130.0\n"]
            assert "OpenAI Codex v0.130.0" in batch.results[0].codex_output

    def test_analyze_image_reverse_prompt_uses_codex_with_uploaded_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uploaded_image = root / "uploads" / "one.png"
            output_root = root / "app-output"
            uploaded_image.parent.mkdir(parents=True, exist_ok=True)
            uploaded_image.write_bytes(b"1")
            runner = CodexImageGenRunner(
                project_root=root,
                codex_generated_root=root / "codex-generated",
                default_output_root=output_root,
            )

            def fake_stream_subprocess_output(command, *, cwd=None, input_text=None, on_output=None):
                assert "--image" in command
                assert str(uploaded_image.resolve()) in command
                assert input_text is not None
                assert "focus on style" in input_text
                assert "prompt engineer" in input_text.lower()
                message_path = Path(command[command.index("--output-last-message") + 1])
                message_path.write_text("3D pixar dragon-ball-inspired expo model, futuristic bodysuit, cinematic lighting", encoding="utf-8")
                output = "Reading prompt from stdin...\nOpenAI Codex v0.130.0\n"
                if on_output is not None:
                    on_output(output)
                return output

            with patch("webapp.stream_subprocess_output", side_effect=fake_stream_subprocess_output):
                result = runner.analyze_image(
                    uploaded_image,
                    analysis_mode="reverse-prompt",
                    user_instruction="focus on style",
                )

            assert result.image_path == uploaded_image.resolve()
            assert result.analysis_mode == "reverse-prompt"
            assert result.output_text == "3D pixar dragon-ball-inspired expo model, futuristic bodysuit, cinematic lighting"
            assert "OpenAI Codex v0.130.0" in result.codex_output


class TestRenderPage:
    def test_renders_form_and_batch_result(self):
        batch = GenerationBatchResult(
            prompt="draw a whale",
            output_dir=Path("output/web"),
            results=[
                GenerationResult(
                    prompt="draw a whale",
                    image_index=1,
                    total_images=2,
                    image_path=Path("output/web/generated-1.png"),
                    assistant_message="done-1",
                    codex_output="Reading prompt from stdin...\nOpenAI Codex v0.130.0",
                ),
                GenerationResult(
                    prompt="draw a whale",
                    image_index=2,
                    total_images=2,
                    image_path=Path("output/web/generated-2.png"),
                    assistant_message="done-2",
                    codex_output="tokens used\n17165",
                ),
            ],
        )

        html = render_page(result=batch)

        assert "<form" in html
        assert "draw a whale" in html
        assert "generated-1.png" in html
        assert "generated-2.png" in html
        assert "output/web" in html
        assert "done-1" in html
        assert "done-2" in html
        assert "OpenAI Codex v0.130.0" in html
        assert "tokens used" in html
        assert 'action="/open-folder"' in html
        assert "Open picture folder" in html

    def test_renders_image_to_text_form_and_result(self):
        html = render_page(
            analysis_result=ImageAnalysisResult(
                image_path=Path("output/uploads/example.png"),
                user_instruction="focus on clothing",
                analysis_mode="reverse-prompt",
                output_text="3D sci-fi model, fitted futuristic bodysuit, expo lighting",
                codex_output="OpenAI Codex v0.130.0",
            )
        )

        assert "Image to text" in html
        assert 'action="/describe-image"' in html
        assert 'name="description_image_upload"' in html
        assert 'name="analysis_mode"' in html
        assert "Reverse prompt" in html
        assert "Structured analysis" in html
        assert "focus on clothing" in html
        assert "fitted futuristic bodysuit" in html
        assert "<strong>Mode:</strong> reverse-prompt" in html
        assert "Use as image prompt" in html
        assert 'data-use-as-image-prompt="3D sci-fi model, fitted futuristic bodysuit, expo lighting"' in html

    def test_renders_structured_analysis_result(self):
        html = render_page(
            analysis_result=ImageAnalysisResult(
                image_path=Path("output/uploads/example.png"),
                user_instruction="extract fields",
                analysis_mode="structured-analysis",
                output_text='{"subject":"Android woman","clothing":"futuristic bodysuit"}',
                codex_output="OpenAI Codex v0.130.0",
            )
        )

        assert "structured-analysis" in html
        assert "&quot;subject&quot;:&quot;Android woman&quot;" in html

    def test_renders_video_open_folder_button(self):
        from webapp import VideoGenerationResult

        html = render_page(
            video_result=VideoGenerationResult(
                prompt="Animate this",
                source_image=Path("output/web/generated-01.png"),
                output_dir=Path("output/videos"),
                video_path=Path("output/videos/generated-01-video.mp4"),
                assistant_message="done-video",
                duration_seconds=4,
                aspect_ratio="16:9",
            )
        )

        assert 'action="/open-folder"' in html
        assert "Open video folder" in html

    def test_renders_loading_script_and_defaults(self):
        html = render_page(current_output_dir="output/web", current_count=3)
        assert 'name="output_dir"' in html
        assert 'value="output/web"' in html
        assert 'name="count"' in html
        assert 'value="3"' in html
        assert 'enctype="multipart/form-data"' in html
        assert 'name="reference_images"' in html
        assert 'id="referenceDropZone"' in html
        assert 'id="referencePreviewGrid"' in html
        assert "paste" in html.lower()
        assert "drag" in html.lower()
        assert "new DataTransfer()" in html
        assert "removeReferenceFile" in html
        assert 'className = "remove-preview-button"' in html
        assert 'button.type = "button"' in html
        assert 'addEventListener("paste"' in html
        assert 'addEventListener("drop"' in html
        assert "Generating…" in html
        assert "submitButton.disabled = true" in html
        assert 'data-use-as-image-prompt' in html
        assert 'promptInput.value = String(button.getAttribute("data-use-as-image-prompt") || "")' in html

    def test_renders_error_message(self):
        html = render_page(error_message="boom")
        assert "boom" in html


class TestUploadValidation:
    def test_sanitizes_upload_filename(self):
        assert sanitize_upload_filename("../../My File.PNG") == "my-file.png"

    def test_rejects_filename_without_supported_extension(self):
        with pytest.raises(ValueError, match="unsupported image type"):
            sanitize_upload_filename("notes.txt")

    def test_saves_reference_images_inside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = save_reference_images(
                project_root=root,
                files=[
                    {
                        "filename": "Ref Image.PNG",
                        "content": b"png-bytes",
                        "content_type": "image/png",
                    }
                ],
            )
            assert len(saved) == 1
            assert saved[0].is_file()
            assert saved[0].suffix == ".png"
            assert "output/uploads" in str(saved[0])


class TestOpenFolderHandler:
    def test_open_folder_uses_finder_for_project_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "output" / "web"
            folder.mkdir(parents=True, exist_ok=True)
            manager = GenerationTaskManager(project_root=root)

            class HandlerHarness(CodexImageGenHandler):
                task_manager = manager

                def __init__(self, *, path: str, body: bytes):
                    self.path = path
                    self.command = "POST"
                    self.rfile = tempfile.SpooledTemporaryFile()
                    self.rfile.write(body)
                    self.rfile.seek(0)
                    from io import BytesIO

                    self.wfile = BytesIO()
                    self.headers = {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Content-Length": str(len(body)),
                    }
                    self.response_status = None

                def send_response(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def send_header(self, keyword: str, value: str) -> None:
                    return

                def end_headers(self) -> None:
                    return

                def send_error(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def log_message(self, format: str, *args: object) -> None:
                    return

            body = b"folder_path=output%2Fweb"
            handler = HandlerHarness(path="/open-folder", body=body)

            with patch("webapp.subprocess.run") as mock_run:
                handler.do_POST()

            mock_run.assert_called_once_with(["open", str(folder.resolve())], check=True)
            assert handler.response_status == 200


class TestSavePromptHandler:
    def test_save_prompt_persists_and_renders_saved_prompts_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)

            class HandlerHarness(CodexImageGenHandler):
                task_manager = manager

                def __init__(self, *, path: str, body: bytes):
                    from io import BytesIO

                    self.path = path
                    self.command = "POST"
                    self.rfile = BytesIO(body)
                    self.wfile = BytesIO()
                    self.headers = {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Content-Length": str(len(body)),
                    }
                    self.response_status = None

                def send_response(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def send_header(self, keyword: str, value: str) -> None:
                    return

                def end_headers(self) -> None:
                    return

                def send_error(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def log_message(self, format: str, *args: object) -> None:
                    return

            handler = HandlerHarness(path="/save-prompt", body=b"prompt=draw%20a%20fox")
            handler.do_POST()

            html = handler.wfile.getvalue().decode("utf-8")
            assert handler.response_status == 200
            assert "Saved prompts" in html
            assert "draw a fox" in html
            assert "Use prompt" in html

    def test_delete_saved_prompts_removes_multiple_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)

            from webapp import save_prompt_entry

            save_prompt_entry(manager.runner.saved_prompts_file, "draw a fox")
            save_prompt_entry(manager.runner.saved_prompts_file, "draw a whale")
            save_prompt_entry(manager.runner.saved_prompts_file, "draw a castle")

            class HandlerHarness(CodexImageGenHandler):
                task_manager = manager

                def __init__(self, *, path: str, body: bytes):
                    from io import BytesIO

                    self.path = path
                    self.command = "POST"
                    self.rfile = BytesIO(body)
                    self.wfile = BytesIO()
                    self.headers = {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Content-Length": str(len(body)),
                    }
                    self.response_status = None

                def send_response(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def send_header(self, keyword: str, value: str) -> None:
                    return

                def end_headers(self) -> None:
                    return

                def send_error(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def log_message(self, format: str, *args: object) -> None:
                    return

            handler = HandlerHarness(
                path="/delete-saved-prompts",
                body=b"saved_prompt=draw%20a%20fox&saved_prompt=draw%20a%20castle",
            )
            handler.do_POST()

            html = handler.wfile.getvalue().decode("utf-8")
            assert handler.response_status == 200
            assert "Deleted 2 saved prompts" in html
            assert "draw a whale" in html
            assert "draw a fox" not in html
            assert "draw a castle" not in html

    def test_delete_history_image_removes_file_and_history_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)
            output_dir = root / "output/web"
            output_dir.mkdir(parents=True)
            image_path = output_dir / "generated-01.png"
            image_path.write_bytes(b"png")
            append_history_entry(
                manager.runner.history_file,
                GenerationBatchResult(
                    prompt="draw a fox",
                    output_dir=output_dir,
                    results=[
                        GenerationResult(
                            prompt="draw a fox",
                            image_index=1,
                            total_images=1,
                            image_path=image_path,
                            assistant_message="done-1",
                        )
                    ],
                ),
            )

            class HandlerHarness(CodexImageGenHandler):
                task_manager = manager

                def __init__(self, *, path: str, body: bytes):
                    from io import BytesIO

                    self.path = path
                    self.command = "POST"
                    self.rfile = BytesIO(body)
                    self.wfile = BytesIO()
                    self.headers = {
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Content-Length": str(len(body)),
                    }
                    self.response_status = None

                def send_response(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def send_header(self, keyword: str, value: str) -> None:
                    return

                def end_headers(self) -> None:
                    return

                def send_error(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def log_message(self, format: str, *args: object) -> None:
                    return

            handler = HandlerHarness(
                path="/delete-history-image",
                body=f"image_path={image_path.as_posix()}".encode("utf-8"),
            )
            handler.do_POST()

            html = handler.wfile.getvalue().decode("utf-8")
            assert handler.response_status == 200
            assert "Deleted 1 history item and removed file" in html
            assert "No history yet." in html
            assert image_path.exists() is False


class TestDescribeImageHandler:
    def test_describe_image_upload_returns_reverse_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = GenerationTaskManager(project_root=root)

            class HandlerHarness(CodexImageGenHandler):
                task_manager = manager

                def __init__(self, *, path: str, body: bytes, content_type: str):
                    self.path = path
                    self.command = "POST"
                    self.rfile = tempfile.SpooledTemporaryFile()
                    self.rfile.write(body)
                    self.rfile.seek(0)
                    from io import BytesIO
                    self.wfile = BytesIO()
                    self.headers = {
                        "Content-Type": content_type,
                        "Content-Length": str(len(body)),
                    }
                    self.response_status = None

                def send_response(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def send_header(self, keyword: str, value: str) -> None:
                    return

                def end_headers(self) -> None:
                    return

                def send_error(self, code: int, message: str | None = None) -> None:
                    self.response_status = code

                def log_message(self, format: str, *args: object) -> None:
                    return

            boundary = "----comic-image-text-boundary"
            body = (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="analysis_mode"\r\n\r\n'
                "reverse-prompt\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="prompt"\r\n\r\n'
                "focus on clothing\r\n"
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="description_image_upload"; filename="ref.png"\r\n'
                "Content-Type: image/png\r\n\r\n"
            ).encode("utf-8") + b"png-bytes\r\n" + f"--{boundary}--\r\n".encode("utf-8")

            fake_result = ImageAnalysisResult(
                image_path=root / "output" / "uploads" / "ref.png",
                user_instruction="focus on clothing",
                analysis_mode="reverse-prompt",
                output_text="futuristic model, glossy bodysuit, expo backdrop",
                codex_output="OpenAI Codex v0.130.0",
            )

            handler = HandlerHarness(
                path="/describe-image",
                body=body,
                content_type=f"multipart/form-data; boundary={boundary}",
            )

            with patch.object(manager.runner, "analyze_image", return_value=fake_result):
                handler.do_POST()

            html = handler.wfile.getvalue().decode("utf-8")
            assert handler.response_status == 200
            assert "glossy bodysuit" in html
