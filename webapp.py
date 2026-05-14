#!/usr/bin/env python3
"""Minimal web UI for Codex CLI image generation."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

PROMPT_MAX_LENGTH = 2000
COUNT_MIN = 1
COUNT_MAX = 8
CODEX_GENERATED_ROOT = Path.home() / ".codex" / "generated_images"
DEFAULT_OUTPUT_DIR = "output/web"
DEFAULT_VIDEO_OUTPUT_DIR = "output/videos"
REMOTION_PROJECT_DIR = "remotion-video"
REMOTION_SOURCE_DIR = "src"
REMOTION_PUBLIC_DIR = "public"
REMOTION_PUBLIC_INPUT_DIR = Path(REMOTION_PUBLIC_DIR) / "input"
REMOTION_ENTRY_FILE = Path(REMOTION_SOURCE_DIR) / "index.ts"
REMOTION_COMPOSITION_ID = "ImageMotionVideo"
DEFAULT_HISTORY_FILE = ".webapp-history.json"
DEFAULT_SAVED_PROMPTS_FILE = ".saved-prompts.json"
DEFAULT_UPLOAD_DIR = "output/uploads"
UPLOAD_MAX_FILES = 4
UPLOAD_MAX_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_UPLOAD_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
VIDEO_DURATION_MIN = 2
VIDEO_DURATION_MAX = 12
DEFAULT_VIDEO_DURATION_SECONDS = 4
DEFAULT_VIDEO_ASPECT_RATIO = "16:9"
ALLOWED_VIDEO_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}
DEFAULT_VIDEO_MOTION_PRESET = "cinematic-push-in"
VIDEO_MOTION_PRESETS: dict[str, dict[str, str]] = {
    "cinematic-push-in": {
        "label": "Cinematic push-in",
        "prompt": "Create a slow cinematic push-in with gentle depth, stable framing, and premium ad-like motion.",
    },
    "parallax-float": {
        "label": "Parallax float",
        "prompt": "Create a subtle parallax float with layered-feeling movement, soft drift, and polished atmospheric motion.",
    },
    "pan-left-to-right": {
        "label": "Pan left to right",
        "prompt": "Create a smooth left-to-right camera pan with elegant pacing and a clean cinematic reveal.",
    },
    "zoom-out-reveal": {
        "label": "Zoom out reveal",
        "prompt": "Start slightly close, then slowly zoom out to reveal more of the composition with cinematic restraint.",
    },
    "orbital-drift": {
        "label": "Orbital drift",
        "prompt": "Create a subtle orbital drift using slight scale, pan, and rotation so the image feels alive without distortion.",
    },
    "custom": {
        "label": "Custom prompt",
        "prompt": "",
    },
}


@dataclass(frozen=True)
class GenerationResult:
    prompt: str
    image_index: int
    total_images: int
    image_path: Path
    assistant_message: str
    codex_output: str = ""


@dataclass(frozen=True)
class GenerationBatchResult:
    prompt: str
    output_dir: Path
    results: list[GenerationResult]


@dataclass(frozen=True)
class VideoGenerationResult:
    prompt: str
    source_image: Path
    output_dir: Path
    video_path: Path
    assistant_message: str
    duration_seconds: int
    aspect_ratio: str


IMAGE_ANALYSIS_MODES: dict[str, str] = {
    "reverse-prompt": "Reverse prompt",
    "structured-analysis": "Structured analysis",
}
DEFAULT_IMAGE_ANALYSIS_MODE = "reverse-prompt"


@dataclass(frozen=True)
class ImageAnalysisResult:
    image_path: Path
    user_instruction: str
    analysis_mode: str
    output_text: str
    codex_output: str


def validate_prompt(prompt: str) -> str:
    cleaned = prompt.strip()
    if not cleaned:
        raise ValueError("prompt must not be empty")
    if len(cleaned) > PROMPT_MAX_LENGTH:
        raise ValueError(f"prompt must be at most {PROMPT_MAX_LENGTH} characters")
    return cleaned


def validate_count(raw_count: str) -> int:
    try:
        count = int(raw_count)
    except ValueError as exc:
        raise ValueError("count must be an integer") from exc
    if not (COUNT_MIN <= count <= COUNT_MAX):
        raise ValueError(f"count must be between {COUNT_MIN} and {COUNT_MAX}")
    return count


def validate_video_duration(raw_duration: str) -> int:
    try:
        duration = int(raw_duration)
    except ValueError as exc:
        raise ValueError("video duration must be an integer") from exc
    if not (VIDEO_DURATION_MIN <= duration <= VIDEO_DURATION_MAX):
        raise ValueError(
            f"video duration must be between {VIDEO_DURATION_MIN} and {VIDEO_DURATION_MAX} seconds"
        )
    return duration


def validate_video_aspect_ratio(raw_aspect_ratio: str) -> str:
    cleaned = raw_aspect_ratio.strip() or DEFAULT_VIDEO_ASPECT_RATIO
    if cleaned not in ALLOWED_VIDEO_ASPECT_RATIOS:
        raise ValueError(
            f"video aspect ratio must be one of {', '.join(sorted(ALLOWED_VIDEO_ASPECT_RATIOS))}"
        )
    return cleaned


def validate_video_motion_preset(raw_motion_preset: str) -> str:
    cleaned = raw_motion_preset.strip() or DEFAULT_VIDEO_MOTION_PRESET
    if cleaned not in VIDEO_MOTION_PRESETS:
        raise ValueError("video motion preset must be a supported option")
    return cleaned


def validate_image_analysis_mode(raw_mode: str) -> str:
    cleaned = raw_mode.strip() or DEFAULT_IMAGE_ANALYSIS_MODE
    if cleaned not in IMAGE_ANALYSIS_MODES:
        raise ValueError("image analysis mode must be a supported option")
    return cleaned


def resolve_video_motion_prompt(motion_preset: str, custom_prompt: str) -> str:
    validated_preset = validate_video_motion_preset(motion_preset)
    cleaned_prompt = custom_prompt.strip()
    if validated_preset == "custom":
        return cleaned_prompt or "Create a subtle cinematic camera move."
    preset_prompt = VIDEO_MOTION_PRESETS[validated_preset]["prompt"]
    if not cleaned_prompt:
        return preset_prompt
    return f"{preset_prompt} Additional direction: {cleaned_prompt}"


def resolve_output_dir(project_root: Path, raw_output_dir: str) -> Path:
    cleaned = raw_output_dir.strip() or DEFAULT_OUTPUT_DIR
    requested = Path(cleaned)
    if requested.is_absolute():
        raise ValueError("output directory must be a relative path")
    candidate = project_root / requested
    resolved = candidate.resolve()
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError("output directory must stay inside the project") from exc
    return candidate


def validate_project_image_file(file_path: Path) -> Path:
    resolved = file_path.resolve()
    if not resolved.is_file():
        raise ValueError("source image file does not exist")
    if resolved.suffix.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError("source image must be a supported image file")
    return resolved


def validate_video_source_path(project_root: Path, raw_source_path: str) -> Path:
    cleaned = raw_source_path.strip()
    if not cleaned:
        raise ValueError("source image path must not be empty")
    relative_path = Path(unquote(cleaned))
    candidate = (project_root.resolve() / relative_path).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError("source image path must stay inside the project") from exc
    return validate_project_image_file(candidate)


def validate_export_destination(raw_destination: str) -> Path:
    cleaned = raw_destination.strip()
    if not cleaned:
        raise ValueError("export destination must not be empty")
    destination = Path(cleaned).expanduser()
    if not destination.exists():
        raise ValueError("export destination must already exist")
    if not destination.is_dir():
        raise ValueError("export destination must be a directory")
    return destination.resolve()


def export_generated_file(source: Path, destination_dir: Path) -> Path:
    if not source.is_file():
        raise ValueError("source image does not exist")
    destination = destination_dir / source.name
    if destination.exists():
        destination = destination_dir / f"{source.stem}-{source.stat().st_mtime_ns}{source.suffix}"
    shutil.copy2(source, destination)
    return destination


def export_generated_batch(sources: list[Path], destination_dir: Path) -> list[Path]:
    if not sources:
        raise ValueError("no source images provided")
    return [export_generated_file(source, destination_dir) for source in sources]


def build_codex_exec_prompt(
    user_prompt: str,
    *,
    image_index: int,
    total_images: int,
    has_reference_images: bool = False,
) -> str:
    reference_instruction = (
        "Use the attached reference image files as visual guidance for subject, style, composition, or details when relevant. "
        if has_reference_images
        else ""
    )
    return (
        "Generate exactly one image using the built-in imagegen tool. "
        "Do not write code. Do not call external image APIs directly. "
        f"This is image {image_index} of {total_images}. "
        "Keep the same core subject and style across the set, but vary composition, pose, framing, or camera angle so each image feels distinct. "
        f"{reference_instruction}"
        "Use this user prompt as the base image request: "
        f"{user_prompt!r}. "
        "After the image is generated, reply with one short sentence only."
    )


def build_codex_image_analysis_prompt(analysis_mode: str, user_instruction: str) -> str:
    validated_mode = validate_image_analysis_mode(analysis_mode)
    cleaned_instruction = user_instruction.strip()
    optional_instruction = (
        f"Pay special attention to this user instruction: {cleaned_instruction!r}. "
        if cleaned_instruction
        else ""
    )
    if validated_mode == "reverse-prompt":
        return (
            "You are an expert image prompt engineer. Analyze the attached image and write a reusable generation prompt that recreates it. "
            "Do not generate an image. Do not write code. "
            f"{optional_instruction}"
            "Return one concise prompt line covering subject, composition, style, lighting, setting, clothing, camera feel, and quality cues."
        )
    return (
        "Analyze the attached image and return a JSON object only. "
        "Do not generate an image. Do not write code. "
        f"{optional_instruction}"
        'Use exactly these top-level keys: "subject", "scene", "composition", "style", "lighting", "color_palette", "clothing", "camera", "notable_details". '
        'Each value must be a short string except "notable_details", which must be an array of short strings.'
    )


def stream_subprocess_output(
    command: list[str],
    *,
    cwd: str | None = None,
    input_text: str | None = None,
    on_output: Callable[[str], None] | None = None,
) -> str:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.PIPE if input_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if input_text is not None and process.stdin is not None:
        process.stdin.write(input_text)
        process.stdin.close()

    output_chunks: list[str] = []
    if process.stdout is not None:
        for chunk in process.stdout:
            output_chunks.append(chunk)
            if on_output is not None:
                on_output(chunk)

    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command, output="".join(output_chunks))
    return "".join(output_chunks).strip()


def build_remotion_render_props(
    *,
    image_file_name: str,
    motion_prompt: str,
    duration_seconds: int,
    aspect_ratio: str,
) -> dict[str, Any]:
    cleaned_motion_prompt = motion_prompt.strip() or "Create a subtle cinematic camera move."
    return {
        "imageFileName": image_file_name,
        "motionPrompt": cleaned_motion_prompt,
        "durationSeconds": duration_seconds,
        "aspectRatio": aspect_ratio,
    }


def sanitize_upload_filename(filename: str) -> str:
    cleaned = Path(filename).name.strip().lower()
    if not cleaned:
        raise ValueError("uploaded image filename must not be empty")
    stem = re.sub(r"[^a-z0-9]+", "-", Path(cleaned).stem).strip("-")
    suffix = Path(cleaned).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError("unsupported image type")
    if not stem:
        stem = "reference"
    return f"{stem}{suffix}"


def save_reference_images(
    *,
    project_root: Path,
    files: list[dict[str, Any]],
    upload_dir: str = DEFAULT_UPLOAD_DIR,
) -> list[Path]:
    if len(files) > UPLOAD_MAX_FILES:
        raise ValueError(f"at most {UPLOAD_MAX_FILES} reference images are allowed")
    destination_root = resolve_output_dir(project_root, upload_dir)
    destination_root.mkdir(parents=True, exist_ok=True)
    saved_files: list[Path] = []
    for file_item in files:
        raw_filename = str(file_item.get("filename", ""))
        payload = file_item.get("content", b"")
        content_type = str(file_item.get("content_type", "")).lower()
        if not isinstance(payload, bytes):
            raise ValueError("uploaded image content must be bytes")
        if not payload:
            continue
        if len(payload) > UPLOAD_MAX_BYTES:
            raise ValueError(f"reference image must be at most {UPLOAD_MAX_BYTES} bytes")
        safe_name = sanitize_upload_filename(raw_filename)
        if content_type and content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
            raise ValueError("unsupported image type")
        destination = destination_root / f"{uuid4().hex}-{safe_name}"
        destination.write_bytes(payload)
        saved_files.append(destination)
    return saved_files


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def append_history_entry(history_file: Path, batch: GenerationBatchResult) -> None:
    entries = load_history_entries(history_file)
    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt": batch.prompt,
        "count": len(batch.results),
        "output_dir": str(batch.output_dir),
        "images": [
            {
                "path": str(item.image_path),
                "assistant_message": item.assistant_message,
                "image_index": item.image_index,
                "total_images": item.total_images,
            }
            for item in batch.results
        ],
    }
    entries.insert(0, entry)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(json.dumps(entries[:50], indent=2, default=_json_default), encoding="utf-8")



def load_history_entries(history_file: Path) -> list[dict[str, Any]]:
    if not history_file.is_file():
        return []
    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    valid_entries: list[dict[str, Any]] = []
    for entry in data:
        if isinstance(entry, dict):
            valid_entries.append(entry)
    return valid_entries


def load_saved_prompts(saved_prompts_file: Path) -> list[dict[str, Any]]:
    if not saved_prompts_file.is_file():
        return []
    try:
        data = json.loads(saved_prompts_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def save_prompt_entry(saved_prompts_file: Path, prompt: str) -> None:
    validated_prompt = validate_prompt(prompt)
    entries = load_saved_prompts(saved_prompts_file)
    deduped_entries = [entry for entry in entries if str(entry.get("prompt", "")) != validated_prompt]
    deduped_entries.insert(
        0,
        {
            "prompt": validated_prompt,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    saved_prompts_file.parent.mkdir(parents=True, exist_ok=True)
    saved_prompts_file.write_text(json.dumps(deduped_entries[:50], indent=2), encoding="utf-8")


def delete_saved_prompts(saved_prompts_file: Path, prompts_to_delete: list[str]) -> int:
    normalized_prompts = {prompt.strip() for prompt in prompts_to_delete if prompt.strip()}
    if not normalized_prompts:
        return 0
    entries = load_saved_prompts(saved_prompts_file)
    remaining_entries = [entry for entry in entries if str(entry.get("prompt", "")).strip() not in normalized_prompts]
    deleted_count = len(entries) - len(remaining_entries)
    saved_prompts_file.parent.mkdir(parents=True, exist_ok=True)
    saved_prompts_file.write_text(json.dumps(remaining_entries[:50], indent=2), encoding="utf-8")
    return deleted_count


def serialize_batch_result(batch: GenerationBatchResult) -> dict[str, Any]:
    return {
        "prompt": batch.prompt,
        "output_dir": str(batch.output_dir),
        "results": [
            {
                "prompt": item.prompt,
                "image_index": item.image_index,
                "total_images": item.total_images,
                "image_path": str(item.image_path),
                "assistant_message": item.assistant_message,
                "codex_output": item.codex_output,
            }
            for item in batch.results
        ],
    }


def serialize_video_result(result: VideoGenerationResult) -> dict[str, Any]:
    return {
        "prompt": result.prompt,
        "output_dir": str(result.output_dir),
        "source_image": str(result.source_image),
        "video_path": str(result.video_path),
        "assistant_message": result.assistant_message,
        "duration_seconds": result.duration_seconds,
        "aspect_ratio": result.aspect_ratio,
    }


class GenerationTaskManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.runner = CodexImageGenRunner(project_root=self.project_root)
        self.video_runner = LocalRemotionVideoRunner(project_root=self.project_root)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_task_record(
        self,
        *,
        prompt: str,
        count: int,
        output_dir: Path,
        reference_images: Optional[list[Path]] = None,
        task_kind: str = "image",
        source_image: Path | None = None,
        duration_seconds: int = DEFAULT_VIDEO_DURATION_SECONDS,
        aspect_ratio: str = DEFAULT_VIDEO_ASPECT_RATIO,
    ) -> str:
        task_id = uuid4().hex
        task = {
            "id": task_id,
            "kind": task_kind,
            "status": "pending",
            "prompt": prompt,
            "count": count,
            "completed_count": 0,
            "output_dir": str(output_dir),
            "reference_images": [str(path) for path in (reference_images or [])],
            "source_image": str(source_image) if source_image is not None else "",
            "duration_seconds": duration_seconds,
            "aspect_ratio": aspect_ratio,
            "error": "",
            "result": None,
            "live_log": "",
            "cancel_requested": False,
        }
        with self._lock:
            self._tasks[task_id] = task
        return task_id

    def start_task(
        self,
        *,
        prompt: str,
        count: int,
        output_dir: Path,
        reference_images: Optional[list[Path]] = None,
        task_kind: str = "image",
        source_image: Path | None = None,
        duration_seconds: int = DEFAULT_VIDEO_DURATION_SECONDS,
        aspect_ratio: str = DEFAULT_VIDEO_ASPECT_RATIO,
    ) -> str:
        task_id = self.create_task_record(
            prompt=prompt,
            count=count,
            output_dir=output_dir,
            reference_images=reference_images,
            task_kind=task_kind,
            source_image=source_image,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
        )
        self._spawn_worker(task_id)
        return task_id

    def _spawn_worker(self, task_id: str) -> None:
        worker = threading.Thread(target=self._run_task, args=(task_id,), daemon=True)
        worker.start()

    def _run_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if task.get("status") == "cancelled":
                return
            task["status"] = "running"
        try:
            if str(task.get("kind", "image")) == "video":
                result = self.video_runner.run(
                    source_image=Path(str(task.get("source_image", ""))),
                    motion_prompt=str(task["prompt"]),
                    output_dir=Path(task["output_dir"]),
                    duration_seconds=int(task.get("duration_seconds", DEFAULT_VIDEO_DURATION_SECONDS)),
                    aspect_ratio=str(task.get("aspect_ratio", DEFAULT_VIDEO_ASPECT_RATIO)),
                    should_cancel=lambda: self._is_cancel_requested(task_id),
                    on_output=lambda chunk: self._append_task_log(task_id, chunk),
                )
            else:
                image_run_kwargs: dict[str, Any] = {
                    "count": int(task["count"]),
                    "output_dir": Path(task["output_dir"]),
                    "should_cancel": lambda: self._is_cancel_requested(task_id),
                    "on_result": lambda item: self._record_task_result(task_id, item),
                    "on_output": lambda chunk: self._append_task_log(task_id, chunk),
                }
                reference_images = [Path(str(path)) for path in task.get("reference_images", [])]
                if reference_images:
                    image_run_kwargs["reference_images"] = reference_images
                result = self.runner.run_batch(task["prompt"], **image_run_kwargs)
        except RuntimeError as exc:
            if str(exc) == "Cancelled by user":
                with self._lock:
                    task["status"] = "cancelled"
                    task["error"] = "Cancelled by user"
                return
            with self._lock:
                task["status"] = "error"
                task["error"] = str(exc)
            return
        except Exception as exc:
            with self._lock:
                task["status"] = "error"
                task["error"] = str(exc)
            return

        with self._lock:
            task["status"] = "completed"
            if isinstance(result, VideoGenerationResult):
                task["completed_count"] = 1
                task["result"] = serialize_video_result(result)
            else:
                task["completed_count"] = len(result.results)
                task["result"] = serialize_batch_result(result)

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            payload = dict(task)
            payload["active_task_count"] = self._active_task_count_locked()
            return json.loads(json.dumps(payload))

    def list_tasks(self, status_filter: str = "all") -> list[dict[str, Any]]:
        with self._lock:
            tasks = list(self._tasks.values())
            active_task_count = self._active_task_count_locked()
        if status_filter != "all":
            tasks = [task for task in tasks if str(task.get("status", "")) == status_filter]
        tasks.sort(key=lambda item: str(item.get("id", "")), reverse=True)
        payload = []
        for task in tasks:
            enriched_task = dict(task)
            enriched_task["active_task_count"] = active_task_count
            payload.append(enriched_task)
        return json.loads(json.dumps(payload))

    def mark_task_running(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task["status"] = "running"

    def cancel_task(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError("task not found")
            if task["status"] == "pending":
                task["status"] = "cancelled"
                task["error"] = "Cancelled by user"
                task["cancel_requested"] = True
                return
            if task["status"] == "running":
                task["status"] = "cancelling"
                task["cancel_requested"] = True
                task["error"] = "Cancellation requested"
                return

    def _is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            return bool(task and task.get("cancel_requested"))

    def _record_task_result(self, task_id: str, result: GenerationResult) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            existing_result = task.get("result")
            if isinstance(existing_result, dict):
                serialized = dict(existing_result)
                serialized_results = list(serialized.get("results", []))
            else:
                serialized = {
                    "prompt": task["prompt"],
                    "output_dir": task["output_dir"],
                    "results": [],
                }
                serialized_results = []
            serialized_results.append(
                {
                    "prompt": result.prompt,
                    "image_index": result.image_index,
                    "total_images": result.total_images,
                    "image_path": str(result.image_path),
                    "assistant_message": result.assistant_message,
                    "codex_output": result.codex_output,
                }
            )
            serialized["results"] = serialized_results
            task["result"] = serialized
            task["completed_count"] = len(serialized_results)

    def _append_task_log(self, task_id: str, chunk: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            existing_log = str(task.get("live_log", ""))
            task["live_log"] = existing_log + chunk

    def _active_task_count_locked(self) -> int:
        return sum(
            1
            for task in self._tasks.values()
            if str(task.get("status", "")) in {"pending", "running", "cancelling"}
        )


class CodexImageGenRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        codex_generated_root: Path = CODEX_GENERATED_ROOT,
        default_output_root: Path | None = None,
        history_file: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.codex_generated_root = codex_generated_root
        self.default_output_root = (
            default_output_root.resolve()
            if default_output_root is not None
            else (self.project_root / DEFAULT_OUTPUT_DIR).resolve()
        )
        self.history_file = history_file or (self.project_root / DEFAULT_HISTORY_FILE)
        self.saved_prompts_file = self.project_root / DEFAULT_SAVED_PROMPTS_FILE
        self._lock = threading.Lock()

    def build_command(
        self,
        prompt: str,
        *,
        image_index: int,
        total_images: int,
        message_file: Path | None = None,
        reference_images: Optional[list[Path]] = None,
    ) -> list[str]:
        target_message_file = message_file or (self.default_output_root / ".last-codex-message.txt")
        target_message_file.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(self.project_root),
            "--output-last-message",
            str(target_message_file),
        ]
        for reference_image in reference_images or []:
            command.extend(["--image", str(reference_image)])
        return command

    def analyze_image(
        self,
        image_path: Path,
        *,
        analysis_mode: str = DEFAULT_IMAGE_ANALYSIS_MODE,
        user_instruction: str = "",
    ) -> ImageAnalysisResult:
        validated_image_path = validate_project_image_file(image_path)
        validated_analysis_mode = validate_image_analysis_mode(analysis_mode)
        message_file = self.default_output_root / ".last-codex-analysis.txt"
        command = self.build_command(
            user_instruction,
            image_index=1,
            total_images=1,
            message_file=message_file,
            reference_images=[validated_image_path],
        )
        codex_output = stream_subprocess_output(
            command,
            input_text=build_codex_image_analysis_prompt(validated_analysis_mode, user_instruction),
        )
        output_text = message_file.read_text(encoding="utf-8").strip()
        if not output_text:
            raise RuntimeError("Codex did not return image analysis output")
        return ImageAnalysisResult(
            image_path=validated_image_path,
            user_instruction=user_instruction.strip(),
            analysis_mode=validated_analysis_mode,
            output_text=output_text,
            codex_output=codex_output,
        )

    def run_batch(
        self,
        prompt: str,
        *,
        count: int,
        output_dir: Path,
        reference_images: Optional[list[Path]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_result: Optional[Callable[[GenerationResult], None]] = None,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> GenerationBatchResult:
        validated_prompt = validate_prompt(prompt)
        if not (COUNT_MIN <= count <= COUNT_MAX):
            raise ValueError(f"count must be between {COUNT_MIN} and {COUNT_MAX}")

        resolved_output_dir = output_dir.resolve()
        try:
            resolved_output_dir.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("output directory must stay inside the project") from exc

        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        message_file = resolved_output_dir / ".last-codex-message.txt"
        results: list[GenerationResult] = []

        with self._lock:
            for image_index in range(1, count + 1):
                if should_cancel is not None and should_cancel():
                    raise RuntimeError("Cancelled by user")
                before = {p.resolve() for p in self._list_generated_images()}
                command = self.build_command(
                    validated_prompt,
                    image_index=image_index,
                    total_images=count,
                    message_file=message_file,
                    reference_images=reference_images,
                )
                streamed_output = stream_subprocess_output(
                    command,
                    input_text=build_codex_exec_prompt(
                        validated_prompt,
                        image_index=image_index,
                        total_images=count,
                        has_reference_images=bool(reference_images),
                    ),
                    on_output=on_output,
                )
                created_image = self._find_new_image(before)
                if created_image is None:
                    raise RuntimeError("No new image was created by Codex imagegen")
                final_path = self._copy_to_output(
                    created_image,
                    output_dir=resolved_output_dir,
                    image_index=image_index,
                )
                assistant_message = message_file.read_text(encoding="utf-8").strip()
                result = GenerationResult(
                    prompt=validated_prompt,
                    image_index=image_index,
                    total_images=count,
                    image_path=final_path,
                    assistant_message=assistant_message,
                    codex_output=streamed_output,
                )
                results.append(result)
                if on_result is not None:
                    on_result(result)
                if should_cancel is not None and should_cancel():
                    raise RuntimeError("Cancelled by user")

        batch = GenerationBatchResult(
            prompt=validated_prompt,
            output_dir=resolved_output_dir,
            results=results,
        )
        append_history_entry(self.history_file, batch)
        return batch

    def _list_generated_images(self) -> list[Path]:
        if not self.codex_generated_root.exists():
            return []
        suffixes = {".png", ".jpg", ".jpeg", ".webp"}
        return [
            path
            for path in self.codex_generated_root.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        ]

    def _find_new_image(self, before: set[Path]) -> Optional[Path]:
        candidates = [path for path in self._list_generated_images() if path.resolve() not in before]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)

    def _copy_to_output(self, source: Path, *, output_dir: Path, image_index: int) -> Path:
        destination = output_dir / f"generated-{image_index:02d}{source.suffix.lower()}"
        if destination.exists():
            destination = output_dir / f"generated-{image_index:02d}-{source.stat().st_mtime_ns}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        return destination


class LocalRemotionVideoRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        default_output_root: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.remotion_project_root = self.project_root / REMOTION_PROJECT_DIR
        self.default_output_root = (
            default_output_root.resolve()
            if default_output_root is not None
            else (self.project_root / DEFAULT_VIDEO_OUTPUT_DIR).resolve()
        )
        self._lock = threading.Lock()

    def build_command(self, *, props_file: Path, output_file: Path) -> list[str]:
        return [
            "npm",
            "run",
            "render",
            "--",
            str((self.remotion_project_root / REMOTION_ENTRY_FILE).resolve()),
            REMOTION_COMPOSITION_ID,
            str(output_file),
            f"--props={props_file}",
        ]

    def run(
        self,
        *,
        source_image: Path,
        motion_prompt: str,
        output_dir: Path,
        duration_seconds: int = DEFAULT_VIDEO_DURATION_SECONDS,
        aspect_ratio: str = DEFAULT_VIDEO_ASPECT_RATIO,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> VideoGenerationResult:
        validated_source_image = validate_project_image_file(source_image)
        validated_duration = validate_video_duration(str(duration_seconds))
        validated_aspect_ratio = validate_video_aspect_ratio(aspect_ratio)
        resolved_output_dir = output_dir.resolve()
        try:
            resolved_output_dir.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("output directory must stay inside the project") from exc
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        video_id = uuid4().hex
        video_path = resolved_output_dir / f"{video_id}.mp4"
        self.ensure_project_scaffold()
        staged_file_name = self._stage_source_image(validated_source_image, video_id=video_id)
        props_file = resolved_output_dir / f"{video_id}.props.json"
        props = build_remotion_render_props(
            image_file_name=staged_file_name,
            motion_prompt=motion_prompt,
            duration_seconds=validated_duration,
            aspect_ratio=validated_aspect_ratio,
        )
        props_file.write_text(json.dumps(props, indent=2), encoding="utf-8")
        self._validate_remotion_installation()

        with self._lock:
            if should_cancel is not None and should_cancel():
                raise RuntimeError("Cancelled by user")
            stream_subprocess_output(
                self.build_command(props_file=props_file, output_file=video_path),
                cwd=str(self.remotion_project_root.resolve()),
                on_output=on_output,
            )
            if should_cancel is not None and should_cancel():
                raise RuntimeError("Cancelled by user")
        if not video_path.is_file():
            raise RuntimeError("No video was created by the local Remotion render")
        return VideoGenerationResult(
            prompt=motion_prompt.strip() or "Create a subtle cinematic camera move.",
            source_image=validated_source_image,
            output_dir=resolved_output_dir,
            video_path=video_path,
            assistant_message="Rendered with local Remotion pipeline",
            duration_seconds=validated_duration,
            aspect_ratio=validated_aspect_ratio,
        )

    def ensure_project_scaffold(self) -> None:
        src_dir = self.remotion_project_root / REMOTION_SOURCE_DIR
        public_input_dir = self.remotion_project_root / REMOTION_PUBLIC_INPUT_DIR
        src_dir.mkdir(parents=True, exist_ok=True)
        public_input_dir.mkdir(parents=True, exist_ok=True)
        self._write_file_if_missing(self.remotion_project_root / "package.json", _build_remotion_package_json())
        self._write_file_if_missing(self.remotion_project_root / "tsconfig.json", _build_remotion_tsconfig_json())
        self._write_file_if_missing(self.remotion_project_root / REMOTION_ENTRY_FILE, _build_remotion_entry_ts())
        self._write_file_if_missing(
            self.remotion_project_root / REMOTION_SOURCE_DIR / "Root.tsx",
            _build_remotion_root_tsx(),
        )
        self._write_file_if_missing(
            self.remotion_project_root / REMOTION_SOURCE_DIR / "ImageMotionVideo.tsx",
            _build_remotion_component_tsx(),
        )

    def _stage_source_image(self, source_image: Path, *, video_id: str) -> str:
        public_input_dir = self.remotion_project_root / REMOTION_PUBLIC_INPUT_DIR
        public_input_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{video_id}{source_image.suffix.lower()}"
        destination = public_input_dir / file_name
        shutil.copy2(source_image, destination)
        return str(Path("input") / file_name)

    def _write_file_if_missing(self, destination: Path, content: str) -> None:
        if destination.exists():
            return
        destination.write_text(content, encoding="utf-8")

    def _validate_remotion_installation(self) -> None:
        remotion_package = self.remotion_project_root / "node_modules" / "remotion" / "package.json"
        if remotion_package.is_file():
            return
        raise RuntimeError(
            "Remotion dependencies are not installed yet. Run `cd remotion-video && npm install` after dependency review."
        )


CodexRemotionVideoRunner = LocalRemotionVideoRunner


def _build_remotion_package_json() -> str:
    return json.dumps(
        {
            "name": "comic-remotion-video",
            "private": True,
            "type": "module",
            "scripts": {
                "render": "remotion render",
                "studio": "remotion studio",
            },
            "dependencies": {
                "react": "^19.1.0",
                "react-dom": "^19.1.0",
                "remotion": "^4.0.366",
            },
            "devDependencies": {
                "@types/react": "^19.1.5",
                "@types/react-dom": "^19.1.5",
                "typescript": "^5.8.3",
            },
        },
        indent=2,
    )


def _build_remotion_tsconfig_json() -> str:
    return json.dumps(
        {
            "compilerOptions": {
                "target": "ES2020",
                "useDefineForClassFields": True,
                "lib": ["DOM", "DOM.Iterable", "ES2020"],
                "allowJs": False,
                "skipLibCheck": True,
                "esModuleInterop": True,
                "allowSyntheticDefaultImports": True,
                "strict": True,
                "forceConsistentCasingInFileNames": True,
                "module": "ESNext",
                "moduleResolution": "Node",
                "resolveJsonModule": True,
                "isolatedModules": True,
                "noEmit": True,
                "jsx": "react-jsx",
            },
            "include": ["src"],
        },
        indent=2,
    )


def _build_remotion_entry_ts() -> str:
    return 'import {registerRoot} from "remotion";\nimport {RemotionRoot} from "./Root";\n\nregisterRoot(RemotionRoot);\n'


def _build_remotion_root_tsx() -> str:
    return """import {Composition} from "remotion";
import {ImageMotionVideo, type ImageMotionVideoProps, calculateImageMotionMetadata} from "./ImageMotionVideo";

export const RemotionRoot = () => {
  return (
    <Composition<ImageMotionVideoProps>
      id="ImageMotionVideo"
      component={ImageMotionVideo}
      durationInFrames={120}
      fps={30}
      width={1280}
      height={720}
      defaultProps={{
        imageFileName: "input/placeholder.png",
        motionPrompt: "Create a subtle cinematic camera move.",
        durationSeconds: 4,
        aspectRatio: "16:9",
      }}
      calculateMetadata={calculateImageMotionMetadata}
    />
  );
};
"""


def _build_remotion_component_tsx() -> str:
    return """import {AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig} from "remotion";

export type ImageMotionVideoProps = {
  imageFileName: string;
  motionPrompt: string;
  durationSeconds: number;
  aspectRatio: "16:9" | "9:16" | "1:1";
};

const resolveDimensions = (aspectRatio: ImageMotionVideoProps["aspectRatio"]) => {
  if (aspectRatio === "9:16") {
    return {width: 1080, height: 1920};
  }
  if (aspectRatio === "1:1") {
    return {width: 1080, height: 1080};
  }
  return {width: 1920, height: 1080};
};

const buildMotionProfile = (motionPrompt: string) => {
  const normalized = motionPrompt.toLowerCase();
  if (normalized.includes("zoom out") || normalized.includes("pull back")) {
    return {startScale: 1.12, endScale: 1, driftX: -24, driftY: 10};
  }
  if (normalized.includes("pan left")) {
    return {startScale: 1.02, endScale: 1.08, driftX: -64, driftY: 0};
  }
  if (normalized.includes("pan right")) {
    return {startScale: 1.02, endScale: 1.08, driftX: 64, driftY: 0};
  }
  return {startScale: 1, endScale: 1.1, driftX: 18, driftY: -12};
};

export const calculateImageMotionMetadata = async ({props}: {props: ImageMotionVideoProps}) => {
  const dimensions = resolveDimensions(props.aspectRatio);
  return {
    durationInFrames: Math.max(1, Math.round(props.durationSeconds * 30)),
    fps: 30,
    width: dimensions.width,
    height: dimensions.height,
  };
};

export const ImageMotionVideo = ({imageFileName, motionPrompt}: ImageMotionVideoProps) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const motion = buildMotionProfile(motionPrompt);
  const progress = interpolate(frame, [0, durationInFrames - 1], [0, 1], {
    easing: Easing.bezier(0.22, 1, 0.36, 1),
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const scale = interpolate(progress, [0, 1], [motion.startScale, motion.endScale]);
  const translateX = interpolate(progress, [0, 1], [0, motion.driftX]);
  const translateY = interpolate(progress, [0, 1], [0, motion.driftY]);
  const overlayOpacity = interpolate(frame, [0, durationInFrames * 0.35, durationInFrames - 1], [0.24, 0.1, 0.2], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const src = staticFile(imageFileName);

  return (
    <AbsoluteFill style={{backgroundColor: "#020617", overflow: "hidden"}}>
      <Img
        src={src}
        style={{
          position: "absolute",
          inset: -80,
          width: "calc(100% + 160px)",
          height: "calc(100% + 160px)",
          objectFit: "cover",
          filter: "blur(42px) brightness(0.6)",
          transform: `scale(${scale + 0.08}) translate(${translateX / 2}px, ${translateY / 2}px)`,
        }}
      />
      <AbsoluteFill
        style={{
          background: `linear-gradient(135deg, rgba(15, 23, 42, ${overlayOpacity}) 0%, rgba(59, 130, 246, ${overlayOpacity * 0.75}) 100%)`,
        }}
      />
      <AbsoluteFill style={{padding: 48, justifyContent: "center", alignItems: "center"}}>
        <Img
          src={src}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "contain",
            borderRadius: 28,
            boxShadow: "0 30px 80px rgba(0, 0, 0, 0.35)",
            transform: `scale(${scale}) translate(${translateX}px, ${translateY}px)`,
          }}
        />
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
"""


def _to_relative_display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _render_media_preview(raw_path: str, alt_text: str) -> str:
    suffix = Path(raw_path).suffix.lower()
    quoted_path = quote(raw_path)
    escaped_path = html.escape(raw_path)
    if suffix in {".mp4", ".webm", ".mov"}:
        return (
            f'<video controls style="max-width: 100%; border-radius: 10px; margin-top: 8px;">'
            f'<source src="/files/{quoted_path}" />'
            f"{escaped_path}</video>"
        )
    return (
        f'<img src="/files/{quoted_path}" alt="{html.escape(alt_text)}" '
        'style="max-width: 180px; border-radius: 10px; margin-top: 8px;" />'
    )


def _render_result_previews(images: list[dict[str, Any]]) -> str:
    previews: list[str] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        raw_path = str(image.get("image_path", "") or image.get("video_path", ""))
        if not raw_path:
            continue
        codex_output = str(image.get("codex_output", ""))
        codex_output_html = (
            f"<details><summary>Codex terminal output</summary><pre>{html.escape(codex_output)}</pre></details>"
            if codex_output
            else ""
        )
        previews.append(
            f'<div>{_render_media_preview(raw_path, "Task preview")}<p>{html.escape(str(image.get("assistant_message", "")))}</p>{codex_output_html}</div>'
        )
    if not previews:
        return ""
    return f"<div style=\"display:flex; gap:12px; flex-wrap:wrap; margin-top:12px;\">{''.join(previews)}</div>"


def _render_reference_image_previews(reference_images: list[str]) -> str:
    if not reference_images:
        return ""
    cards: list[str] = []
    for raw_path in reference_images:
        if not raw_path:
            continue
        cards.append(
            f'<div><img src="/files/{quote(raw_path)}" alt="Reference image" style="max-width: 180px; border-radius: 10px; margin-top: 8px;" /><p style="word-break:break-all;">{html.escape(raw_path)}</p></div>'
        )
    if not cards:
        return ""
    return (
        "<div style=\"margin-top:12px;\"><p><strong>Reference images</strong></p>"
        f"<div style=\"display:flex; gap:12px; flex-wrap:wrap;\">{''.join(cards)}</div></div>"
    )



def render_history_page(entries: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for entry in entries:
        raw_prompt = str(entry.get("prompt", ""))
        prompt = html.escape(raw_prompt)
        output_dir = html.escape(str(entry.get("output_dir", "")))
        created_at = html.escape(str(entry.get("created_at", "")))
        count = html.escape(str(entry.get("count", 0)))
        images_html = []
        for image in entry.get("images", []):
            if not isinstance(image, dict):
                continue
            raw_path = str(image.get("path", ""))
            path = html.escape(raw_path, quote=True)
            assistant_message = html.escape(str(image.get("assistant_message", "")))
            images_html.append(
                f"<li><a href=\"/files/{quote(raw_path)}\">{path}</a> — {assistant_message}<br /><img src=\"/files/{quote(raw_path)}\" alt=\"History thumbnail\" style=\"max-width: 220px; margin-top: 8px; border-radius: 10px;\" /></li>"
            )
        sections.append(
            f"""
            <section class=\"card\">
              <p><strong>Prompt:</strong> {prompt}</p>
              <button type="button" data-copy-history-prompt="{html.escape(raw_prompt, quote=True)}">Copy prompt</button>
              <a href="/?prompt={quote(raw_prompt)}">Use prompt</a>
              <p><strong>Created:</strong> {created_at}</p>
              <p><strong>Count:</strong> {count}</p>
              <p><strong>Output directory:</strong> {output_dir}</p>
              <ul>{''.join(images_html)}</ul>
            </section>
            """
        )

    body = "".join(sections) or "<section class=\"card\"><p>No history yet.</p></section>"
    return f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>History</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #111827; color: #f9fafb; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; }}
    .card {{ background: #1f2937; border-radius: 16px; padding: 20px; margin-top: 20px; }}
    a {{ color: #93c5fd; }}
  </style>
</head>
<body>
  <main>
    <h1>History</h1>
    <p><a href=\"/\">← Back to generator</a></p>
    {body}
  </main>
  <script>
    document.querySelectorAll("[data-copy-history-prompt]").forEach((button) => {{
      button.addEventListener("click", async () => {{
        const prompt = String(button.getAttribute("data-copy-history-prompt") || "");
        await navigator.clipboard.writeText(prompt);
        const originalText = button.textContent;
        button.textContent = "Copied!";
        setTimeout(() => {{
          button.textContent = originalText;
        }}, 1200);
      }});
    }});
  </script>
</body>
</html>
"""


def render_saved_prompts_page(entries: list[dict[str, Any]], info_message: str = "") -> str:
    items: list[str] = []
    for entry in entries:
        raw_prompt = str(entry.get("prompt", ""))
        if not raw_prompt:
            continue
        items.append(
            f"""
            <li>
              <label>
                <input type="checkbox" name="saved_prompt" value="{html.escape(raw_prompt, quote=True)}" />
                Select
              </label>
              <code>{html.escape(raw_prompt)}</code>
              <button type="button" data-use-saved-prompt="{html.escape(raw_prompt, quote=True)}">Use prompt</button>
              <a href="/?prompt={quote(raw_prompt)}">Open in generator</a>
            </li>
            """
        )

    body = (
        f"""
        <section class="card">
          <form method="post" action="/delete-saved-prompts">
            <ul>{''.join(items)}</ul>
            <button type="submit">Delete selected</button>
          </form>
        </section>
        """
        if items
        else '<section class="card"><p>No saved prompts yet.</p></section>'
    )
    info_html = f"<p class=\"info\">{html.escape(info_message)}</p>" if info_message else ""
    return f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Saved prompts</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #111827; color: #f9fafb; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; }}
    .card {{ background: #1f2937; border-radius: 16px; padding: 20px; margin-top: 20px; }}
    a {{ color: #93c5fd; }}
    .info {{ color: #86efac; font-weight: 600; }}
    code {{ word-break: break-word; }}
    li {{ margin-top: 12px; }}
  </style>
</head>
<body>
  <main>
    <h1>Saved prompts</h1>
    <p><a href=\"/\">← Back to generator</a></p>
    {info_html}
    {body}
  </main>
  <script>
    document.querySelectorAll("[data-use-saved-prompt]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const prompt = String(button.getAttribute("data-use-saved-prompt") || "");
        window.location.href = "/?prompt=" + encodeURIComponent(prompt);
      }});
    }});
  </script>
</body>
</html>
"""


def render_task_page(task: dict[str, Any]) -> str:
    task_id = html.escape(str(task.get("id", "")))
    task_kind = str(task.get("kind", "image"))
    status = str(task.get("status", "pending"))
    prompt = html.escape(str(task.get("prompt", "")))
    count = int(task.get("count", 0) or 0)
    completed_count = int(task.get("completed_count", 0) or 0)
    active_task_count = int(task.get("active_task_count", 0) or 0)
    live_log = html.escape(str(task.get("live_log", "")))
    reference_images = [str(item) for item in task.get("reference_images", []) if str(item)]
    reference_html = _render_reference_image_previews(reference_images)
    source_image = str(task.get("source_image", ""))
    source_image_html = (
        "<div style=\"margin-top:12px;\"><p><strong>Source image</strong></p>"
        f"{_render_media_preview(source_image, 'Source image')}</div>"
        if source_image
        else ""
    )
    result = task.get("result") if isinstance(task.get("result"), dict) else {}
    preview_html = _render_result_previews(list(result.get("results", []))) if isinstance(result, dict) else ""

    if status in {"pending", "running", "cancelling"}:
        progress_html = ""
        if task_kind == "image" and count > 0:
            progress_html = f"<p><strong>Completed {completed_count} / {count}</strong></p>"
        if task_kind == "video":
            progress_html = "<p><strong>Rendering one video with Remotion</strong></p>"
        active_tasks_html = ""
        if active_task_count > 0:
            active_tasks_html = f"<p><strong>Parallel active tasks: {active_task_count}</strong></p>"
        return f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Task {task_id}</title>
</head>
<body style=\"font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #111827; color: #f9fafb; padding: 32px;\">
  <h1>{"Rendering video in background" if task_kind == "video" else "Generating in background"}</h1>
  <p><strong>Prompt:</strong> {prompt}</p>
  {reference_html}
  {source_image_html}
  {active_tasks_html}
  {progress_html}
  <p>{"This page refreshes automatically. The finished video will appear below." if task_kind == "video" else "This page refreshes automatically. Each finished image will appear below immediately."}</p>
  {preview_html}
  <details open>
    <summary>Live task log</summary>
    <pre id="taskLiveLog" style="white-space: pre-wrap; background: #0f172a; padding: 12px; border-radius: 10px;">{live_log}</pre>
  </details>
  <p><a href=\"/\" style=\"color:#93c5fd;\">← Back</a></p>
  <p style=\"display:none;\">/tasks/{task_id}/status</p>
  <script>
    const taskLog = document.getElementById("taskLiveLog");
    const pollTaskStatus = async () => {{
      try {{
        const response = await fetch("/tasks/{task_id}/status", {{ cache: "no-store" }});
        if (!response.ok) {{
          window.location.reload();
          return;
        }}
        const payload = await response.json();
        if (taskLog) {{
          taskLog.textContent = String(payload.live_log || "");
        }}
        if (!["pending", "running", "cancelling"].includes(String(payload.status || ""))) {{
          window.location.reload();
          return;
        }}
      }} catch (_error) {{
        window.location.reload();
        return;
      }}
      setTimeout(pollTaskStatus, 1000);
    }};
    setTimeout(pollTaskStatus, 1000);
  </script>
</body>
</html>
"""

    if status == "error":
        return render_page(error_message=str(task.get("error", "Task failed")))

    if task_kind == "video":
        return render_page(
            video_result=VideoGenerationResult(
                prompt=str(result.get("prompt", task.get("prompt", ""))),
                source_image=Path(str(result.get("source_image", task.get("source_image", "")))),
                output_dir=Path(str(result.get("output_dir", task.get("output_dir", DEFAULT_VIDEO_OUTPUT_DIR)))),
                video_path=Path(str(result.get("video_path", ""))),
                assistant_message=str(result.get("assistant_message", "")),
                duration_seconds=int(result.get("duration_seconds", DEFAULT_VIDEO_DURATION_SECONDS)),
                aspect_ratio=str(result.get("aspect_ratio", DEFAULT_VIDEO_ASPECT_RATIO)),
            )
        )

    results = result.get("results") or []
    batch = GenerationBatchResult(
        prompt=str(result.get("prompt", task.get("prompt", ""))),
        output_dir=Path(str(result.get("output_dir", task.get("output_dir", DEFAULT_OUTPUT_DIR)))),
        results=[
            GenerationResult(
                prompt=str(item.get("prompt", task.get("prompt", ""))),
                image_index=int(item.get("image_index", 1)),
                total_images=int(item.get("total_images", len(results) or 1)),
                image_path=Path(str(item.get("image_path", ""))),
                assistant_message=str(item.get("assistant_message", "")),
                codex_output=str(item.get("codex_output", "")),
            )
            for item in results
            if isinstance(item, dict)
        ],
    )
    return render_page(result=batch)


def render_task_list_page(tasks: list[dict[str, Any]], active_status: str = "all") -> str:
    global_active_task_count = max((int(task.get("active_task_count", 0) or 0) for task in tasks), default=0)
    if active_status != "all":
        tasks = [task for task in tasks if str(task.get("status", "")) == active_status]
    active_task_count = global_active_task_count or sum(
        1 for task in tasks if str(task.get("status", "")) in {"pending", "running", "cancelling"}
    )
    has_active_task = active_task_count > 0
    if not tasks:
        body = "<section class=\"card\"><p>No tasks yet.</p></section>"
    else:
        items: list[str] = []
        for task in tasks:
            task_id = html.escape(str(task.get("id", "")))
            status = html.escape(str(task.get("status", "")))
            kind = html.escape(str(task.get("kind", "image")))
            prompt = html.escape(str(task.get("prompt", "")))
            count = html.escape(str(task.get("count", "")))
            output_dir = html.escape(str(task.get("output_dir", "")))
            completed_count_raw = int(task.get("completed_count", 0) or 0)
            progress_html = ""
            if str(task.get("status", "")) in {"pending", "running", "cancelling"} and kind == "image":
                progress_html = (
                    f"<p><strong>Completed {completed_count_raw} / {html.escape(str(task.get('count', 0)))}</strong></p>"
                )
            preview_html = ""
            result = task.get("result")
            if isinstance(result, dict):
                if kind == "video" and str(result.get("video_path", "")):
                    preview_html = _render_result_previews([result])
                else:
                    preview_html = _render_result_previews(list(result.get("results", [])))
            cancel_html = ""
            if str(task.get("status", "")) in {"pending", "running", "cancelling"}:
                cancel_html = f"""
                <form method=\"post\" action=\"/tasks/{task_id}/cancel\">
                  <button type=\"submit\">Cancel task</button>
                </form>
                """
            items.append(
                f"""
                <section class=\"card\">
                  <p><strong>Task:</strong> <a href=\"/tasks/{task_id}\">{task_id}</a></p>
                  <p><strong>Status:</strong> {status}</p>
                  <p><strong>Kind:</strong> {kind}</p>
                  <p><strong>Prompt:</strong> {prompt}</p>
                  <p><strong>Count:</strong> {count}</p>
                  {progress_html}
                  <p><strong>Output directory:</strong> {output_dir}</p>
                  {cancel_html}
                  {preview_html}
                </section>
                """
            )
        body = "".join(items)

    auto_refresh_script = ""
    if has_active_task:
        auto_refresh_script = """
  <script>
    setTimeout(() => {
      window.location.reload();
    }, 2000);
  </script>
"""

    return f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Tasks</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #111827; color: #f9fafb; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; }}
    .card {{ background: #1f2937; border-radius: 16px; padding: 20px; margin-top: 20px; }}
    a {{ color: #93c5fd; }}
  </style>
</head>
<body>
  <main>
    <h1>Tasks</h1>
    <p><a href=\"/\">← Back to generator</a></p>
    <p><strong>Active tasks: {active_task_count}</strong></p>
    <p><strong>Filter: {html.escape(active_status)}</strong></p>
    <p>
      <a href=\"/tasks?status=all\">all</a> ·
      <a href=\"/tasks?status=pending\">pending</a> ·
      <a href=\"/tasks?status=running\">running</a> ·
      <a href=\"/tasks?status=completed\">completed</a> ·
      <a href=\"/tasks?status=cancelled\">cancelled</a> ·
      <a href=\"/tasks?status=error\">error</a>
    </p>
    {body}
  </main>
{auto_refresh_script}
</body>
</html>
"""


def render_page(
    *,
    result: Optional[GenerationBatchResult] = None,
    video_result: Optional[VideoGenerationResult] = None,
    analysis_result: Optional[ImageAnalysisResult] = None,
    error_message: str = "",
    info_message: str = "",
    current_output_dir: str = DEFAULT_OUTPUT_DIR,
    current_video_output_dir: str = DEFAULT_VIDEO_OUTPUT_DIR,
    current_count: int = 1,
    current_prompt: str = "",
    current_description_prompt: str = "",
    current_image_analysis_mode: str = DEFAULT_IMAGE_ANALYSIS_MODE,
    current_video_prompt: str = "",
    current_video_motion_preset: str = DEFAULT_VIDEO_MOTION_PRESET,
    current_video_source_path: str = "",
    current_video_duration: int = DEFAULT_VIDEO_DURATION_SECONDS,
    current_video_aspect_ratio: str = DEFAULT_VIDEO_ASPECT_RATIO,
    current_export_dir: str = "",
) -> str:
    try:
        selected_video_motion_preset = validate_video_motion_preset(current_video_motion_preset)
    except ValueError:
        selected_video_motion_preset = DEFAULT_VIDEO_MOTION_PRESET
    video_motion_preset_options = "".join(
        (
            f'<option value="{html.escape(preset_value, quote=True)}"'
            f'{" selected" if preset_value == selected_video_motion_preset else ""}>'
            f"{html.escape(preset_data['label'])}</option>"
        )
        for preset_value, preset_data in VIDEO_MOTION_PRESETS.items()
    )
    try:
        selected_image_analysis_mode = validate_image_analysis_mode(current_image_analysis_mode)
    except ValueError:
        selected_image_analysis_mode = DEFAULT_IMAGE_ANALYSIS_MODE
    image_analysis_mode_options = "".join(
        (
            f'<option value="{html.escape(mode_value, quote=True)}"'
            f'{" selected" if mode_value == selected_image_analysis_mode else ""}>'
            f"{html.escape(mode_label)}</option>"
        )
        for mode_value, mode_label in IMAGE_ANALYSIS_MODES.items()
    )
    result_html = ""
    if result is not None:
        cards = []
        hidden_inputs = []
        for item in result.results:
            relative_image_path = _to_relative_display_path(item.image_path)
            image_url = f"/files/{quote(relative_image_path)}"
            hidden_inputs.append(
                f'<input type="hidden" name="image_path" value="{html.escape(relative_image_path, quote=True)}" />'
            )
            cards.append(
                f"""
                <article class=\"result-item\">
                  <p><strong>Image {item.image_index}/{item.total_images}</strong></p>
                  <p><strong>Codex:</strong> {html.escape(item.assistant_message)}</p>
                  <details>
                    <summary>Codex terminal output</summary>
                    <pre>{html.escape(item.codex_output)}</pre>
                  </details>
                  <img src=\"{image_url}\" alt=\"Generated image {item.image_index}\" />
                  <button type=\"button\" data-use-as-video-source data-image-path=\"{html.escape(relative_image_path, quote=True)}\">Use as video source</button>
                  <form method=\"post\" action=\"/export\" class=\"export-form\">
                    <input type=\"hidden\" name=\"image_path\" value=\"{html.escape(relative_image_path, quote=True)}\" />
                    <label>Export directory</label>
                    <input name=\"export_dir\" value=\"{html.escape(current_export_dir, quote=True)}\" placeholder=\"/Users/you/Desktop/exports\" />
                    <button type=\"submit\">Export image</button>
                  </form>
                </article>
                """
            )
        result_html = f"""
        <section class=\"card\">
          <h2>Generated images</h2>
          <p><strong>Prompt:</strong> {html.escape(result.prompt)}</p>
          <p><strong>Output directory:</strong> {html.escape(str(result.output_dir))}</p>
          <form method=\"post\" action=\"/open-folder\" class=\"export-form\">
            <input type=\"hidden\" name=\"folder_path\" value=\"{html.escape(_to_relative_display_path(result.output_dir), quote=True)}\" />
            <button type=\"submit\">Open picture folder</button>
          </form>
          <form method=\"post\" action=\"/export-batch\" class=\"export-batch-form\">
            <label>Export all images to directory</label>
            <input name=\"export_dir\" value=\"{html.escape(current_export_dir, quote=True)}\" placeholder=\"/Users/you/Desktop/exports\" />
            {''.join(hidden_inputs)}
            <button type=\"submit\">Export all images</button>
          </form>
          <div class=\"result-grid\">{''.join(cards)}</div>
        </section>
        """

    video_result_html = ""
    if video_result is not None:
        relative_video_path = _to_relative_display_path(video_result.video_path)
        relative_source_path = _to_relative_display_path(video_result.source_image)
        video_result_html = f"""
        <section class=\"card\">
          <h2>Generated video</h2>
          <p><strong>Motion prompt:</strong> {html.escape(video_result.prompt)}</p>
          <p><strong>Source image:</strong> {html.escape(relative_source_path)}</p>
          <p><strong>Output directory:</strong> {html.escape(str(video_result.output_dir))}</p>
          <p><strong>Duration:</strong> {video_result.duration_seconds}s · <strong>Aspect ratio:</strong> {html.escape(video_result.aspect_ratio)}</p>
          <p><strong>Renderer:</strong> {html.escape(video_result.assistant_message)}</p>
          <form method=\"post\" action=\"/open-folder\" class=\"export-form\">
            <input type=\"hidden\" name=\"folder_path\" value=\"{html.escape(_to_relative_display_path(video_result.output_dir), quote=True)}\" />
            <button type=\"submit\">Open video folder</button>
          </form>
          <video controls>
            <source src="/files/{quote(relative_video_path)}" />
          </video>
          <form method=\"post\" action=\"/export\" class=\"export-form\">
            <input type=\"hidden\" name=\"image_path\" value=\"{html.escape(relative_video_path, quote=True)}\" />
            <label>Export directory</label>
            <input name=\"export_dir\" value=\"{html.escape(current_export_dir, quote=True)}\" placeholder=\"/Users/you/Desktop/exports\" />
            <button type=\"submit\">Export video</button>
          </form>
        </section>
        """

    analysis_result_html = ""
    if analysis_result is not None:
        relative_description_image_path = _to_relative_display_path(analysis_result.image_path)
        use_as_image_prompt_button_html = (
            f'<button type="button" data-use-as-image-prompt="{html.escape(analysis_result.output_text, quote=True)}">Use as image prompt</button>'
            if analysis_result.analysis_mode == "reverse-prompt"
            else ""
        )
        analysis_result_html = f"""
        <section class=\"card\">
          <h2>Image to text result</h2>
          <p><strong>Mode:</strong> {html.escape(analysis_result.analysis_mode)}</p>
          <p><strong>Instruction:</strong> {html.escape(analysis_result.user_instruction or "Analyze the image")}</p>
          <pre>{html.escape(analysis_result.output_text)}</pre>
          {use_as_image_prompt_button_html}
          {_render_media_preview(relative_description_image_path, "Uploaded image")}
          <details>
            <summary>Codex terminal output</summary>
            <pre>{html.escape(analysis_result.codex_output)}</pre>
          </details>
        </section>
        """

    error_html = f"<p class=\"error\">{html.escape(error_message)}</p>" if error_message else ""
    info_html = f"<p class=\"info\">{html.escape(info_message)}</p>" if info_message else ""

    return f"""
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Codex ImageGen Web UI</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 0; background: #111827; color: #f9fafb; }}
    main {{ max-width: 960px; margin: 0 auto; padding: 32px 20px 64px; }}
    .card {{ background: #1f2937; border-radius: 16px; padding: 20px; margin-top: 20px; }}
    textarea, input, select {{ width: 100%; border-radius: 12px; padding: 14px; border: 1px solid #374151; background: #111827; color: #f9fafb; box-sizing: border-box; }}
    textarea {{ min-height: 140px; }}
    .row {{ display: grid; grid-template-columns: 1fr 180px; gap: 12px; margin-top: 12px; }}
    button {{ margin-top: 12px; padding: 12px 18px; border: 0; border-radius: 12px; background: #2563eb; color: white; font-size: 16px; cursor: pointer; }}
    button:disabled {{ opacity: 0.7; cursor: wait; }}
    img {{ width: 100%; border-radius: 14px; margin-top: 12px; display: block; }}
    .error {{ color: #fca5a5; font-weight: 600; }}
    .info {{ color: #86efac; font-weight: 600; }}
    .hint {{ color: #cbd5e1; }}
    .status {{ display: none; margin-top: 12px; color: #93c5fd; font-weight: 600; }}
    .result-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }}
    .result-item {{ background: #111827; border-radius: 14px; padding: 12px; }}
    .top-links {{ display: flex; gap: 16px; align-items: center; }}
    .top-links a {{ color: #93c5fd; }}
    .export-form {{ margin-top: 12px; }}
    video {{ width: 100%; border-radius: 14px; margin-top: 12px; display: block; background: #020617; }}
    .upload-drop-zone {{ margin-top: 12px; border: 1px dashed #60a5fa; border-radius: 14px; padding: 16px; background: rgba(37, 99, 235, 0.08); }}
    .upload-drop-zone.is-dragover {{ background: rgba(37, 99, 235, 0.18); border-color: #93c5fd; }}
    .preview-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-top: 12px; }}
    .preview-item {{ background: #111827; border: 1px solid #374151; border-radius: 12px; padding: 10px; position: relative; }}
    .preview-item img {{ margin-top: 0; width: 100%; height: auto; max-height: 260px; object-fit: contain; background: #0f172a; }}
    .preview-item p {{ margin: 8px 0 0; font-size: 12px; word-break: break-all; color: #cbd5e1; }}
    .remove-preview-button {{ position: absolute; top: 8px; right: 8px; margin-top: 0; padding: 4px 8px; border-radius: 999px; background: #991b1b; font-size: 12px; }}
  </style>
</head>
<body>
  <main>
    <div class=\"top-links\">
      <h1>Codex ImageGen Web UI</h1>
      <a href=\"/history\">History</a>
      <a href=\"/tasks\">Tasks</a>
      <a href=\"/saved-prompts\">Saved prompts</a>
    </div>
    <p class=\"hint\">This page calls <code>codex exec</code> and asks Codex to use its built-in imagegen tool.</p>
    <section class=\"card\">
      <form method=\"post\" action=\"/generate\" id=\"generateForm\" enctype=\"multipart/form-data\">
        <label for=\"prompt\">Prompt</label>
        <textarea id=\"prompt\" name=\"prompt\" maxlength=\"{PROMPT_MAX_LENGTH}\" placeholder=\"Describe the image you want\">{html.escape(current_prompt)}</textarea>
        <div style=\"margin-top: 12px;\">
          <label for=\"reference_images\">Reference images</label>
          <input id=\"reference_images\" name=\"reference_images\" type=\"file\" accept=\".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp\" multiple />
          <p class=\"hint\">Optional. Upload up to {UPLOAD_MAX_FILES} images and Codex will use them as visual references with your prompt.</p>
          <div id=\"referenceDropZone\" class=\"upload-drop-zone\" tabindex=\"0\">
            Drag reference images here, or paste screenshots directly into this box.
          </div>
          <div id=\"referencePreviewGrid\" class=\"preview-grid\"></div>
        </div>
        <div class=\"row\">
          <div>
            <label for=\"output_dir\">Output directory</label>
            <input id=\"output_dir\" name=\"output_dir\" value=\"{html.escape(current_output_dir, quote=True)}\" />
          </div>
          <div>
            <label for=\"count\">Image count (max {COUNT_MAX})</label>
            <input id=\"count\" name=\"count\" type=\"number\" min=\"{COUNT_MIN}\" max=\"{COUNT_MAX}\" value=\"{current_count}\" />
          </div>
        </div>
        <button type=\"submit\" id=\"submitButton\">Generate</button>
        <button type="submit" formaction="/save-prompt" formmethod="post">Save current prompt</button>
        <p class=\"status\" id=\"statusMessage\">Generating… This can take a little while.</p>
      </form>
      {error_html}
      {info_html}
    </section>
    <section class=\"card\">
      <h2>Image to video</h2>
      <p class=\"hint\">Upload an image or reuse a generated project image. The backend will use the fixed local Remotion project in <code>remotion-video/</code> and render one MP4.</p>
      <form method=\"post\" action=\"/generate-video\" id=\"videoForm\" enctype=\"multipart/form-data\">
        <label for=\"motion_preset\">Motion style</label>
        <select id=\"motion_preset\" name=\"motion_preset\">
          {video_motion_preset_options}
        </select>
        <label for=\"video_prompt\">Motion prompt</label>
        <textarea id=\"video_prompt\" name=\"prompt\" maxlength=\"{PROMPT_MAX_LENGTH}\" placeholder=\"Describe the camera move, animation, or atmosphere\">{html.escape(current_video_prompt)}</textarea>
        <div style=\"margin-top: 12px;\">
          <label for=\"source_image_upload\">Upload source image</label>
          <input id=\"source_image_upload\" name=\"source_image_upload\" type=\"file\" accept=\".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp\" />
          <p class=\"hint\">Optional if you instead choose a project image path below.</p>
        </div>
        <div class=\"row\">
          <div>
            <label for=\"source_image_path\">Or use an existing project image path</label>
            <input id=\"source_image_path\" name=\"source_image_path\" value=\"{html.escape(current_video_source_path, quote=True)}\" placeholder=\"output/web/generated-01.png\" />
          </div>
          <div>
            <label for=\"video_duration_seconds\">Duration seconds</label>
            <input id=\"video_duration_seconds\" name=\"duration_seconds\" type=\"number\" min=\"{VIDEO_DURATION_MIN}\" max=\"{VIDEO_DURATION_MAX}\" value=\"{current_video_duration}\" />
          </div>
        </div>
        <div class=\"row\">
          <div>
            <label for=\"video_output_dir\">Output directory</label>
            <input id=\"video_output_dir\" name=\"output_dir\" value=\"{html.escape(current_video_output_dir, quote=True)}\" />
          </div>
          <div>
            <label for=\"aspect_ratio\">Aspect ratio</label>
            <input id=\"aspect_ratio\" name=\"aspect_ratio\" value=\"{html.escape(current_video_aspect_ratio, quote=True)}\" placeholder=\"16:9\" />
          </div>
        </div>
        <button type=\"submit\" id=\"videoSubmitButton\">Render video</button>
        <p class=\"status\" id=\"videoStatusMessage\">Rendering video… This can take a while.</p>
      </form>
    </section>
    {result_html}
    <section class=\"card\">
      <h2>Image to text</h2>
      <p class=\"hint\">Upload one image and let Codex either reverse-engineer a prompt or extract structured visual fields.</p>
      <form method=\"post\" action=\"/describe-image\" id=\"describeImageForm\" enctype=\"multipart/form-data\">
        <label for=\"analysis_mode\">Analysis mode</label>
        <select id=\"analysis_mode\" name=\"analysis_mode\">
          {image_analysis_mode_options}
        </select>
        <label for=\"description_prompt\">Optional instruction</label>
        <textarea id=\"description_prompt\" name=\"prompt\" maxlength=\"{PROMPT_MAX_LENGTH}\" placeholder=\"For example: focus on outfit, style, pose, and scene\">{html.escape(current_description_prompt)}</textarea>
        <div style=\"margin-top: 12px;\">
          <label for=\"description_image_upload\">Upload source image</label>
          <input id=\"description_image_upload\" name=\"description_image_upload\" type=\"file\" accept=\".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp\" />
        </div>
        <button type=\"submit\" id=\"describeImageSubmitButton\">Analyze image</button>
        <p class=\"status\" id=\"describeImageStatusMessage\">Analyzing image… This can take a little while.</p>
      </form>
    </section>
    {video_result_html}
    {analysis_result_html}
  </main>
  <script>
    const form = document.getElementById("generateForm");
    const promptInput = document.getElementById("prompt");
    const submitButton = document.getElementById("submitButton");
    const statusMessage = document.getElementById("statusMessage");
    const videoForm = document.getElementById("videoForm");
    const videoSubmitButton = document.getElementById("videoSubmitButton");
    const videoStatusMessage = document.getElementById("videoStatusMessage");
    const describeImageForm = document.getElementById("describeImageForm");
    const describeImageSubmitButton = document.getElementById("describeImageSubmitButton");
    const describeImageStatusMessage = document.getElementById("describeImageStatusMessage");
    const videoSourcePathInput = document.getElementById("source_image_path");
    const referenceInput = document.getElementById("reference_images");
    const referenceDropZone = document.getElementById("referenceDropZone");
    const referencePreviewGrid = document.getElementById("referencePreviewGrid");
    const maxReferenceFiles = {UPLOAD_MAX_FILES};

    function getCurrentReferenceFiles() {{
      return Array.from(referenceInput.files || []);
    }}

    function updateReferenceFiles(files) {{
      const deduped = [];
      for (const file of files) {{
        if (!file.type.startsWith("image/")) {{
          continue;
        }}
        const exists = deduped.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified);
        if (!exists) {{
          deduped.push(file);
        }}
        if (deduped.length >= maxReferenceFiles) {{
          break;
        }}
      }}
      const transfer = new DataTransfer();
      for (const file of deduped) {{
        transfer.items.add(file);
      }}
      referenceInput.files = transfer.files;
      renderReferencePreviews();
    }}

    function mergeReferenceFiles(extraFiles) {{
      updateReferenceFiles([...getCurrentReferenceFiles(), ...extraFiles]);
    }}

    function removeReferenceFile(indexToRemove) {{
      const remainingFiles = getCurrentReferenceFiles().filter((_, index) => index !== indexToRemove);
      updateReferenceFiles(remainingFiles);
    }}

    function renderReferencePreviews() {{
      referencePreviewGrid.innerHTML = "";
      const files = getCurrentReferenceFiles();
      if (!files.length) {{
        referencePreviewGrid.innerHTML = '<p class="hint">No reference images selected yet.</p>';
        return;
      }}
      for (const file of files) {{
        const item = document.createElement("div");
        item.className = "preview-item";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "remove-preview-button";
        button.textContent = "Remove";
        button.addEventListener("click", () => {{
          removeReferenceFile(files.indexOf(file));
        }});
        const img = document.createElement("img");
        img.alt = file.name;
        const label = document.createElement("p");
        label.textContent = file.name;
        item.appendChild(button);
        item.appendChild(img);
        item.appendChild(label);
        referencePreviewGrid.appendChild(item);
        const reader = new FileReader();
        reader.onload = (event) => {{
          img.src = String(event.target?.result || "");
        }};
        reader.readAsDataURL(file);
      }}
    }}

    form.addEventListener("submit", () => {{
      submitButton.disabled = true;
      submitButton.textContent = "Generating…";
      statusMessage.style.display = "block";
    }});
    videoForm.addEventListener("submit", () => {{
      videoSubmitButton.disabled = true;
      videoSubmitButton.textContent = "Rendering…";
      videoStatusMessage.style.display = "block";
    }});
    describeImageForm.addEventListener("submit", () => {{
      describeImageSubmitButton.disabled = true;
      describeImageSubmitButton.textContent = "Describing…";
      describeImageStatusMessage.style.display = "block";
    }});
    referenceInput.addEventListener("change", () => {{
      updateReferenceFiles(getCurrentReferenceFiles());
    }});
    ["dragenter", "dragover"].forEach((eventName) => {{
      referenceDropZone.addEventListener(eventName, (event) => {{
        event.preventDefault();
        referenceDropZone.classList.add("is-dragover");
      }});
    }});
    ["dragleave", "dragend"].forEach((eventName) => {{
      referenceDropZone.addEventListener(eventName, () => {{
        referenceDropZone.classList.remove("is-dragover");
      }});
    }});
    referenceDropZone.addEventListener("drop", (event) => {{
      event.preventDefault();
      referenceDropZone.classList.remove("is-dragover");
      mergeReferenceFiles(Array.from(event.dataTransfer?.files || []));
    }});
    referenceDropZone.addEventListener("paste", (event) => {{
      const pastedFiles = Array.from(event.clipboardData?.files || []);
      if (!pastedFiles.length) {{
        return;
      }}
      event.preventDefault();
      mergeReferenceFiles(pastedFiles);
    }});
    document.querySelectorAll("[data-use-as-video-source]").forEach((button) => {{
      button.addEventListener("click", () => {{
        videoSourcePathInput.value = String(button.getAttribute("data-image-path") || "");
        videoSourcePathInput.scrollIntoView({{ behavior: "smooth", block: "center" }});
      }});
    }});
    document.querySelectorAll("[data-use-as-image-prompt]").forEach((button) => {{
      button.addEventListener("click", () => {{
        promptInput.value = String(button.getAttribute("data-use-as-image-prompt") || "");
        promptInput.scrollIntoView({{ behavior: "smooth", block: "center" }});
        promptInput.focus();
      }});
    }});
    renderReferencePreviews();
  </script>
</body>
</html>
"""


class CodexImageGenHandler(BaseHTTPRequestHandler):
    task_manager: GenerationTaskManager

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            current_prompt = parse_qs(parsed.query).get("prompt", [""])[0]
            self._send_html(render_page(current_prompt=current_prompt))
            return
        if parsed.path == "/history":
            self._send_html(render_history_page(load_history_entries(self.task_manager.runner.history_file)))
            return
        if parsed.path == "/saved-prompts":
            self._send_html(render_saved_prompts_page(load_saved_prompts(self.task_manager.runner.saved_prompts_file)))
            return
        if parsed.path == "/tasks":
            status_filter = parse_qs(parsed.query).get("status", ["all"])[0]
            self._send_html(
                render_task_list_page(
                    self.task_manager.list_tasks(status_filter=status_filter),
                    active_status=status_filter,
                )
            )
            return
        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/status"):
            task_id = parsed.path.removeprefix("/tasks/").removesuffix("/status").strip("/")
            try:
                self._send_json(self._require_task(task_id))
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/tasks/"):
            task_id = parsed.path.removeprefix("/tasks/").strip("/")
            try:
                self._send_html(render_task_page(self._require_task(task_id)))
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        if parsed.path.startswith("/files/"):
            self._send_file(parsed.path.removeprefix("/files/"))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/generate":
            self._handle_generate()
            return
        if parsed.path == "/generate-video":
            self._handle_generate_video()
            return
        if parsed.path == "/describe-image":
            self._handle_describe_image()
            return
        if parsed.path.startswith("/tasks/") and parsed.path.endswith("/cancel"):
            task_id = parsed.path.removeprefix("/tasks/").removesuffix("/cancel").strip("/")
            self._handle_cancel_task(task_id)
            return
        if parsed.path == "/export":
            self._handle_export()
            return
        if parsed.path == "/export-batch":
            self._handle_export_batch()
            return
        if parsed.path == "/save-prompt":
            self._handle_save_prompt()
            return
        if parsed.path == "/delete-saved-prompts":
            self._handle_delete_saved_prompts()
            return
        if parsed.path == "/open-folder":
            self._handle_open_folder()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_generate(self) -> None:
        params, uploads = self._read_form_submission()
        prompt = params.get("prompt", [""])[0]
        output_dir_raw = params.get("output_dir", [DEFAULT_OUTPUT_DIR])[0]
        count_raw = params.get("count", ["1"])[0]

        try:
            validated_prompt = validate_prompt(prompt)
            count = validate_count(count_raw)
            output_dir = resolve_output_dir(self.task_manager.project_root, output_dir_raw)
            reference_images = save_reference_images(
                project_root=self.task_manager.project_root,
                files=uploads.get("reference_images", []),
            )
            task_id = self.task_manager.start_task(
                prompt=validated_prompt,
                count=count,
                output_dir=output_dir,
                reference_images=reference_images,
            )
        except Exception as exc:
            safe_count = COUNT_MIN
            try:
                safe_count = int(count_raw)
            except ValueError:
                pass
            self._send_html(
                render_page(
                    error_message=str(exc),
                    current_output_dir=output_dir_raw or DEFAULT_OUTPUT_DIR,
                    current_count=min(max(safe_count, COUNT_MIN), COUNT_MAX),
                    current_prompt=prompt,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_html(render_task_page(self._require_task(task_id)), status=HTTPStatus.ACCEPTED)

    def _handle_generate_video(self) -> None:
        params, uploads = self._read_form_submission()
        motion_prompt = params.get("prompt", [""])[0]
        motion_preset_raw = params.get("motion_preset", [DEFAULT_VIDEO_MOTION_PRESET])[0]
        output_dir_raw = params.get("output_dir", [DEFAULT_VIDEO_OUTPUT_DIR])[0]
        source_image_path_raw = params.get("source_image_path", [""])[0]
        duration_raw = params.get("duration_seconds", [str(DEFAULT_VIDEO_DURATION_SECONDS)])[0]
        aspect_ratio_raw = params.get("aspect_ratio", [DEFAULT_VIDEO_ASPECT_RATIO])[0]

        try:
            output_dir = resolve_output_dir(self.task_manager.project_root, output_dir_raw)
            duration_seconds = validate_video_duration(duration_raw)
            aspect_ratio = validate_video_aspect_ratio(aspect_ratio_raw)
            motion_preset = validate_video_motion_preset(motion_preset_raw)
            resolved_motion_prompt = resolve_video_motion_prompt(motion_preset, motion_prompt)
            source_image = self._resolve_video_source_image(
                raw_source_path=source_image_path_raw,
                uploads=uploads.get("source_image_upload", []),
            )
            task_id = self.task_manager.start_task(
                prompt=resolved_motion_prompt,
                count=1,
                output_dir=output_dir,
                task_kind="video",
                source_image=source_image,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
            )
        except Exception as exc:
            safe_duration = DEFAULT_VIDEO_DURATION_SECONDS
            try:
                safe_duration = int(duration_raw)
            except ValueError:
                pass
            self._send_html(
                render_page(
                    error_message=str(exc),
                    current_video_output_dir=output_dir_raw or DEFAULT_VIDEO_OUTPUT_DIR,
                    current_video_prompt=motion_prompt,
                    current_video_motion_preset=motion_preset_raw or DEFAULT_VIDEO_MOTION_PRESET,
                    current_video_source_path=source_image_path_raw,
                    current_video_duration=min(max(safe_duration, VIDEO_DURATION_MIN), VIDEO_DURATION_MAX),
                    current_video_aspect_ratio=aspect_ratio_raw or DEFAULT_VIDEO_ASPECT_RATIO,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_html(render_task_page(self._require_task(task_id)), status=HTTPStatus.ACCEPTED)

    def _handle_save_prompt(self) -> None:
        params, _uploads = self._read_form_submission()
        prompt = params.get("prompt", [""])[0]
        try:
            save_prompt_entry(self.task_manager.runner.saved_prompts_file, prompt)
        except Exception as exc:
            self._send_html(
                render_page(
                    error_message=str(exc),
                    current_prompt=prompt,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_html(
            render_saved_prompts_page(
                load_saved_prompts(self.task_manager.runner.saved_prompts_file),
                info_message="Prompt saved",
            )
        )

    def _handle_delete_saved_prompts(self) -> None:
        params = self._read_form_params()
        selected_prompts = params.get("saved_prompt", [])
        deleted_count = delete_saved_prompts(self.task_manager.runner.saved_prompts_file, selected_prompts)
        info_message = (
            f"Deleted {deleted_count} saved prompts"
            if deleted_count
            else "No saved prompts selected"
        )
        self._send_html(
            render_saved_prompts_page(
                load_saved_prompts(self.task_manager.runner.saved_prompts_file),
                info_message=info_message,
            )
        )

    def _handle_describe_image(self) -> None:
        params, uploads = self._read_form_submission()
        user_instruction = params.get("prompt", [""])[0]
        analysis_mode_raw = params.get("analysis_mode", [DEFAULT_IMAGE_ANALYSIS_MODE])[0]

        try:
            analysis_mode = validate_image_analysis_mode(analysis_mode_raw)
            saved_uploads = save_reference_images(
                project_root=self.task_manager.project_root,
                files=uploads.get("description_image_upload", [])[:1],
            )
            if not saved_uploads:
                raise ValueError("please upload one image to describe")
            result = self.task_manager.runner.analyze_image(
                saved_uploads[0],
                analysis_mode=analysis_mode,
                user_instruction=user_instruction,
            )
        except Exception as exc:
            self._send_html(
                render_page(
                    error_message=str(exc),
                    current_description_prompt=user_instruction,
                    current_image_analysis_mode=analysis_mode_raw,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_html(
            render_page(
                analysis_result=result,
                current_description_prompt=user_instruction,
                current_image_analysis_mode=analysis_mode_raw,
            )
        )

    def _handle_export(self) -> None:
        params = self._read_form_params()
        image_path_raw = params.get("image_path", [""])[0]
        export_dir_raw = params.get("export_dir", [""])[0]

        try:
            source = self._resolve_project_file(image_path_raw)
            destination_dir = validate_export_destination(export_dir_raw)
            exported = export_generated_file(source, destination_dir)
        except Exception as exc:
            self._send_html(
                render_page(
                    error_message=str(exc),
                    current_export_dir=export_dir_raw,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_html(
            render_page(
                info_message=f"Exported to {exported}",
                current_export_dir=export_dir_raw,
            )
        )

    def _handle_cancel_task(self, task_id: str) -> None:
        try:
            self.task_manager.cancel_task(task_id)
        except Exception as exc:
            self._send_html(render_page(error_message=str(exc)), status=HTTPStatus.BAD_REQUEST)
            return
        self._send_html(
            render_task_list_page(
                self.task_manager.list_tasks(),
                active_status="all",
            ),
            status=HTTPStatus.OK,
        )

    def _handle_export_batch(self) -> None:
        params = self._read_form_params()
        image_paths_raw = params.get("image_path", [])
        export_dir_raw = params.get("export_dir", [""])[0]

        try:
            sources = [self._resolve_project_file(image_path) for image_path in image_paths_raw]
            destination_dir = validate_export_destination(export_dir_raw)
            exported = export_generated_batch(sources, destination_dir)
        except Exception as exc:
            self._send_html(
                render_page(
                    error_message=str(exc),
                    current_export_dir=export_dir_raw,
                ),
                status=HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_html(
            render_page(
                info_message=f"Exported {len(exported)} images to {destination_dir}",
                current_export_dir=export_dir_raw,
            )
        )

    def _handle_open_folder(self) -> None:
        params = self._read_form_params()
        folder_path_raw = params.get("folder_path", [""])[0]
        try:
            folder_path = self._resolve_project_directory(folder_path_raw)
            subprocess.run(["open", str(folder_path)], check=True)
        except Exception as exc:
            self._send_html(render_page(error_message=str(exc)), status=HTTPStatus.BAD_REQUEST)
            return

        self._send_html(render_page(info_message=f"Opened Finder for {folder_path}"))

    def _read_form_params(self) -> dict[str, list[str]]:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        return parse_qs(body)

    def _read_form_submission(self) -> tuple[dict[str, list[str]], dict[str, list[dict[str, Any]]]]:
        content_type = self.headers.get("Content-Type", "")
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        if content_type.startswith("multipart/form-data"):
            return self._parse_multipart_form(body=body, content_type=content_type)
        return parse_qs(body.decode("utf-8")), {}

    def _parse_multipart_form(
        self,
        *,
        body: bytes,
        content_type: str,
    ) -> tuple[dict[str, list[str]], dict[str, list[dict[str, Any]]]]:
        message = BytesParser(policy=email_policy).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        )
        params: dict[str, list[str]] = {}
        uploads: dict[str, list[dict[str, Any]]] = {}
        for part in message.iter_parts():
            field_name = part.get_param("name", header="content-disposition")
            if not field_name:
                continue
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename:
                uploads.setdefault(field_name, []).append(
                    {
                        "filename": filename,
                        "content": payload,
                        "content_type": part.get_content_type(),
                    }
                )
                continue
            params.setdefault(field_name, []).append(payload.decode("utf-8"))
        return params, uploads

    def _resolve_project_file(self, raw_relative_path: str) -> Path:
        file_path = self._resolve_project_path(raw_relative_path, error_label="image path")
        if not file_path.is_file():
            raise ValueError("image file does not exist")
        return file_path

    def _resolve_project_directory(self, raw_folder_path: str) -> Path:
        folder_path = self._resolve_project_path(raw_folder_path, error_label="folder path")
        if not folder_path.is_dir():
            raise ValueError("folder does not exist")
        return folder_path

    def _resolve_project_path(self, raw_path: str, *, error_label: str) -> Path:
        cleaned = unquote(raw_path.strip())
        if not cleaned:
            raise ValueError(f"{error_label} must not be empty")
        requested_path = Path(cleaned)
        candidate = (
            requested_path.resolve()
            if requested_path.is_absolute()
            else (self.task_manager.project_root / requested_path).resolve()
        )
        try:
            candidate.relative_to(self.task_manager.project_root)
        except ValueError as exc:
            raise ValueError(f"{error_label} must stay inside the project") from exc
        return candidate

    def _resolve_video_source_image(self, *, raw_source_path: str, uploads: list[dict[str, Any]]) -> Path:
        saved_uploads = save_reference_images(
            project_root=self.task_manager.project_root,
            files=uploads[:1],
        )
        if saved_uploads:
            return validate_project_image_file(saved_uploads[0])
        return validate_video_source_path(self.task_manager.project_root, raw_source_path)

    def _require_task(self, task_id: str) -> dict[str, Any]:
        task = self.task_manager.get_task(task_id)
        if task is None:
            raise ValueError("task not found")
        return task

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload_obj: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(payload_obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_file(self, raw_relative_path: str) -> None:
        try:
            file_path = self._resolve_project_file(raw_relative_path)
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        payload = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)



def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Codex imagegen web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, dest="output_dir")
    return parser.parse_args(argv)



def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_root = Path.cwd().resolve()
    task_manager = GenerationTaskManager(project_root=project_root)
    task_manager.runner.default_output_root = resolve_output_dir(project_root, args.output_dir).resolve()
    handler = type("BoundCodexImageGenHandler", (CodexImageGenHandler,), {"task_manager": task_manager})
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        print(f"Serving Codex imagegen UI on http://{args.host}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
