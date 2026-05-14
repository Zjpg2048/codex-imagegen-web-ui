"""Sequential DALL-E 3 caller and file saver."""

from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ImageResult:
    index: int
    prompt: str
    status: str          # "ok" | "error"
    file_path: str = ""
    error: str = ""


def run_generation_batch(
    prompts: list[str],
    out_dir: str,
    client: Any = None,        # openai.OpenAI instance; injected for testing
    size: str = "1024x1024",
    quality: str = "standard",
) -> list[ImageResult]:
    """
    Call DALL-E 3 once per prompt, save each image to out_dir.
    Never aborts on a single failure — marks that slot as error and continues.
    """
    if client is None:
        import openai  # deferred so tests can mock without installing openai
        client = openai.OpenAI()

    os.makedirs(out_dir, exist_ok=True)
    results: list[ImageResult] = []

    for i, prompt in enumerate(prompts):
        result = ImageResult(index=i + 1, prompt=prompt, status="error")
        try:
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                n=1,
                size=size,
                quality=quality,
            )
            image_url = response.data[0].url
            file_path = os.path.join(out_dir, f"image_{i + 1:02d}.png")
            urllib.request.urlretrieve(image_url, file_path)
            result.status = "ok"
            result.file_path = file_path
        except Exception as exc:
            result.error = str(exc)
        results.append(result)

    return results
