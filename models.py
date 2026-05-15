from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class ImageAnalysisResult:
    image_path: Path
    user_instruction: str
    analysis_mode: str
    analysis_agent: str
    output_text: str
    codex_output: str
