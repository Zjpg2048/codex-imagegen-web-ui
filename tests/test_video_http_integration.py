"""HTTP-style integration test for the image-to-video flow."""

from __future__ import annotations

import json
from io import BytesIO
import re
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from webapp import CodexImageGenHandler, GenerationTaskManager, VideoGenerationResult


def _multipart_form_data(fields: dict[str, str], boundary: str) -> bytes:
    lines: list[bytes] = []
    for name, value in fields.items():
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"'.encode("utf-8"),
                b"",
                value.encode("utf-8"),
            ]
        )
    lines.append(f"--{boundary}--".encode("utf-8"))
    lines.append(b"")
    return b"\r\n".join(lines)


def test_generate_video_http_flow_completes_and_returns_video_status():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_image = root / "output" / "web" / "generated-01.png"
        source_image.parent.mkdir(parents=True, exist_ok=True)
        source_image.write_bytes(b"png")
        manager = GenerationTaskManager(project_root=root)

        def fake_video_run(
            *,
            source_image: Path,
            motion_prompt: str,
            output_dir: Path,
            duration_seconds: int,
            aspect_ratio: str,
            should_cancel=None,
            on_output=None,
        ) -> VideoGenerationResult:
            if should_cancel is not None and should_cancel():
                raise RuntimeError("Cancelled by user")
            time.sleep(0.05)
            output_dir.mkdir(parents=True, exist_ok=True)
            video_path = output_dir / "test-video.mp4"
            video_path.write_bytes(b"mp4")
            assert "parallax" in motion_prompt.lower()
            assert "Slow cinematic push-in with gentle parallax." in motion_prompt
            return VideoGenerationResult(
                prompt=motion_prompt,
                source_image=source_image,
                output_dir=output_dir,
                video_path=video_path,
                assistant_message="Rendered with local Remotion pipeline",
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
            )

        class HandlerHarness(CodexImageGenHandler):
            task_manager = manager

            def __init__(self, *, path: str, method: str, body: bytes, content_type: str):
                self.path = path
                self.command = method
                self.rfile = BytesIO(body)
                self.wfile = BytesIO()
                self.headers = {
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                }
                self.response_status = None
                self.response_headers: list[tuple[str, str]] = []

            def send_response(self, code: int, message: str | None = None) -> None:
                self.response_status = code

            def send_header(self, keyword: str, value: str) -> None:
                self.response_headers.append((keyword, value))

            def end_headers(self) -> None:
                return

            def send_error(self, code: int, message: str | None = None) -> None:
                self.response_status = code

            def log_message(self, format: str, *args: object) -> None:
                return

        with patch.object(manager.video_runner, "run", side_effect=fake_video_run):
            boundary = "----comic-video-test-boundary"
            payload = _multipart_form_data(
                {
                    "prompt": "Slow cinematic push-in with gentle parallax.",
                    "motion_preset": "parallax-float",
                    "source_image_path": "output/web/generated-01.png",
                    "duration_seconds": "4",
                    "aspect_ratio": "16:9",
                    "output_dir": "output/videos",
                },
                boundary,
            )
            post_handler = HandlerHarness(
                path="/generate-video",
                method="POST",
                body=payload,
                content_type=f"multipart/form-data; boundary={boundary}",
            )
            post_handler.do_POST()
            html = post_handler.wfile.getvalue().decode("utf-8")

            assert post_handler.response_status == 202
            task_id_match = re.search(r"/tasks/([a-f0-9]+)/status", html)
            if task_id_match is not None:
                task_id = task_id_match.group(1)
            else:
                task_id = str(manager.list_tasks()[0]["id"])

            status_payload: dict[str, object] = {}
            for _ in range(20):
                get_handler = HandlerHarness(
                    path=f"/tasks/{task_id}/status",
                    method="GET",
                    body=b"",
                    content_type="application/json",
                )

                get_handler.do_GET()
                status_payload = json.loads(get_handler.wfile.getvalue().decode("utf-8"))
                if status_payload.get("status") == "completed":
                    break
                time.sleep(0.05)

            assert status_payload["status"] == "completed"
            assert status_payload["kind"] == "video"
            assert status_payload["completed_count"] == 1
            assert str(status_payload["result"]["video_path"]).endswith("test-video.mp4")
