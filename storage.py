from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from models import GenerationBatchResult

if TYPE_CHECKING:
    from models import ImageAnalysisResult

DEFAULT_ANALYSIS_HISTORY_FILE = ".webapp-analysis-history.json"
ANALYSIS_HISTORY_MAX = 100
GENERATION_HISTORY_MAX = 50
SAVED_PROMPTS_MAX = 50


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
    history_file.write_text(json.dumps(entries[:GENERATION_HISTORY_MAX], indent=2, default=_json_default), encoding="utf-8")


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
    validated_prompt = prompt.strip()
    if not validated_prompt:
        raise ValueError("prompt must not be empty")
    if len(validated_prompt) > 2000:
        raise ValueError("prompt too long")
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
    saved_prompts_file.write_text(json.dumps(deduped_entries[:SAVED_PROMPTS_MAX], indent=2), encoding="utf-8")


def update_saved_prompt_entry(saved_prompts_file: Path, original_prompt: str, updated_prompt: str) -> None:
    validated_original_prompt = original_prompt.strip()
    if not validated_original_prompt:
        raise ValueError("prompt must not be empty")
    if len(validated_original_prompt) > 2000:
        raise ValueError("prompt too long")
    validated_updated_prompt = updated_prompt.strip()
    if not validated_updated_prompt:
        raise ValueError("prompt must not be empty")
    if len(validated_updated_prompt) > 2000:
        raise ValueError("prompt too long")
    entries = load_saved_prompts(saved_prompts_file)
    matched_entry: dict[str, Any] | None = None
    remaining_entries: list[dict[str, Any]] = []
    for entry in entries:
        entry_prompt = str(entry.get("prompt", ""))
        if entry_prompt == validated_original_prompt and matched_entry is None:
            matched_entry = entry
            continue
        if entry_prompt == validated_updated_prompt:
            continue
        remaining_entries.append(entry)
    if matched_entry is None:
        raise ValueError("saved prompt not found")
    remaining_entries.insert(
        0,
        {
            "prompt": validated_updated_prompt,
            "created_at": str(matched_entry.get("created_at") or datetime.now(timezone.utc).isoformat()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    saved_prompts_file.parent.mkdir(parents=True, exist_ok=True)
    saved_prompts_file.write_text(json.dumps(remaining_entries[:SAVED_PROMPTS_MAX], indent=2), encoding="utf-8")


def delete_saved_prompts(saved_prompts_file: Path, prompts_to_delete: list[str]) -> int:
    normalized_prompts = {prompt.strip() for prompt in prompts_to_delete if prompt.strip()}
    if not normalized_prompts:
        return 0
    entries = load_saved_prompts(saved_prompts_file)
    remaining_entries = [entry for entry in entries if str(entry.get("prompt", "")).strip() not in normalized_prompts]
    deleted_count = len(entries) - len(remaining_entries)
    saved_prompts_file.parent.mkdir(parents=True, exist_ok=True)
    saved_prompts_file.write_text(json.dumps(remaining_entries[:SAVED_PROMPTS_MAX], indent=2), encoding="utf-8")
    return deleted_count


def _resolve_history_image_path(project_root: Path, raw_path: str) -> Path | None:
    cleaned = raw_path.strip()
    if not cleaned:
        return None
    requested_path = Path(cleaned)
    candidate = (requested_path if requested_path.is_absolute() else project_root / requested_path).resolve()
    try:
        candidate.relative_to(project_root.resolve())
    except ValueError:
        return None
    return candidate


def delete_history_image(history_file: Path, project_root: Path, image_path: Path) -> dict[str, Any]:
    target_path = image_path.resolve()
    entries = load_history_entries(history_file)
    updated_entries: list[dict[str, Any]] = []
    deleted_history_count = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_images = entry.get("images", [])
        if not isinstance(raw_images, list):
            updated_entries.append(entry)
            continue

        remaining_images: list[dict[str, Any]] = []
        entry_deleted_count = 0
        for image in raw_images:
            if not isinstance(image, dict):
                continue
            resolved_image_path = _resolve_history_image_path(project_root, str(image.get("path", "")))
            if resolved_image_path == target_path:
                deleted_history_count += 1
                entry_deleted_count += 1
                continue
            remaining_images.append(image)

        if entry_deleted_count:
            if remaining_images:
                updated_entries.append({**entry, "images": remaining_images, "count": len(remaining_images)})
            continue
        updated_entries.append(entry)

    if deleted_history_count:
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text(json.dumps(updated_entries[:GENERATION_HISTORY_MAX], indent=2), encoding="utf-8")

    deleted_file = False
    if target_path.exists():
        if not target_path.is_file():
            raise ValueError("image path must point to a file")
        target_path.unlink()
        deleted_file = True

    return {
        "deleted_history_count": deleted_history_count,
        "deleted_file": deleted_file,
    }


def load_analysis_history(history_file: Path) -> list[dict[str, Any]]:
    if not history_file.is_file():
        return []
    try:
        data = json.loads(history_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def append_analysis_entry(history_file: Path, result: "ImageAnalysisResult") -> None:
    entries = load_analysis_history(history_file)
    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "image_path": str(result.image_path),
        "analysis_mode": result.analysis_mode,
        "analysis_agent": result.analysis_agent,
        "user_instruction": result.user_instruction,
        "output_text": result.output_text,
    }
    entries.insert(0, entry)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(json.dumps(entries[:ANALYSIS_HISTORY_MAX], indent=2), encoding="utf-8")
