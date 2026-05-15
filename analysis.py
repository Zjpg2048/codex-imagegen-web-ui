from __future__ import annotations

import base64
import json
import mimetypes
import subprocess
from pathlib import Path

from models import ImageAnalysisResult

IMAGE_ANALYSIS_MODES: dict[str, str] = {
    "reverse-prompt": "Reverse prompt",
    "structured-analysis": "Structured analysis",
}
DEFAULT_IMAGE_ANALYSIS_MODE = "reverse-prompt"

IMAGE_ANALYSIS_AGENTS: dict[str, str] = {
    "claude": "Claude",
    "codex": "Codex",
}
DEFAULT_IMAGE_ANALYSIS_AGENT = "claude"
CLAUDE_ANALYSIS_MODEL = "claude-haiku-4-5-20251001"


def validate_image_analysis_mode(raw_mode: str) -> str:
    cleaned = raw_mode.strip() or DEFAULT_IMAGE_ANALYSIS_MODE
    if cleaned not in IMAGE_ANALYSIS_MODES:
        raise ValueError("image analysis mode must be a supported option")
    return cleaned


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


def analyze_image_with_claude(
    image_path: Path,
    *,
    analysis_mode: str,
    user_instruction: str,
) -> ImageAnalysisResult:
    image_bytes = image_path.read_bytes()
    mime_type, _ = mimetypes.guess_type(str(image_path))
    if mime_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        mime_type = "image/jpeg"
    b64_data = base64.standard_b64encode(image_bytes).decode()

    prompt_text = build_codex_image_analysis_prompt(analysis_mode, user_instruction)

    stream_msg = json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64_data}},
                {"type": "text", "text": prompt_text},
            ],
        },
    })

    proc = subprocess.run(
        ["claude", "-p", "--verbose", "--model", CLAUDE_ANALYSIS_MODEL, "--input-format", "stream-json", "--output-format", "stream-json"],
        input=stream_msg,
        capture_output=True,
        text=True,
        timeout=120,
    )
    cli_output = proc.stdout

    output_text = ""
    for line in cli_output.splitlines():
        try:
            obj = json.loads(line)
            if obj.get("type") == "result":
                output_text = obj.get("result", "").strip()
                break
        except (json.JSONDecodeError, AttributeError):
            pass

    if not output_text:
        raise RuntimeError(f"Claude CLI did not return output. stderr: {proc.stderr[:500]}")

    return ImageAnalysisResult(
        image_path=image_path,
        user_instruction=user_instruction.strip(),
        analysis_mode=analysis_mode,
        analysis_agent="claude",
        output_text=output_text,
        codex_output="",
    )
