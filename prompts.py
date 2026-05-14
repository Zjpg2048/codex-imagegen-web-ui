"""Prompt assembly for multi-image generation."""

STYLE_LOCK = "same character, consistent art style, single subject"

ANTI_COLLAGE = (
    "single subject only, one pose only, "
    "not a character sheet, not a collage, not a grid, not a multi-view sheet"
)

VARIATION_TABLES: dict[str, list[str]] = {
    "pose": [
        "standing upright, front view",
        "sitting, three-quarter view",
        "running, side view",
        "crouching, dynamic angle",
        "jumping, low angle shot",
        "resting, back view",
        "kneeling, profile view",
        "turning mid-step, over-the-shoulder view",
    ],
    "composition": [
        "close-up portrait, centered framing",
        "medium shot, rule of thirds",
        "wide shot, environmental context",
        "extreme close-up, face detail",
        "full body, symmetrical composition",
        "dutch angle, dramatic framing",
        "silhouette framing, strong negative space",
        "foreground framing, layered depth",
    ],
    "color": [
        "warm golden palette, sunset tones",
        "cool blue palette, moonlight tones",
        "monochrome, black and white",
        "vibrant saturated colors, pop art style",
        "muted pastel tones, soft lighting",
        "high contrast, deep shadows and bright highlights",
        "earth-tone palette, natural ambient light",
        "neon palette, reflective night lighting",
    ],
    "camera": [
        "eye-level angle, neutral perspective",
        "bird's eye view, looking down",
        "worm's eye view, looking up",
        "over-the-shoulder perspective",
        "extreme low angle, heroic perspective",
        "isometric view, game-style angle",
        "telephoto compression, distant framing",
        "wide-angle lens, dramatic perspective stretch",
    ],
}

VALID_MODES = list(VARIATION_TABLES.keys())
COUNT_MIN = 1
COUNT_MAX = 8


def build_prompts(prompt: str, count: int, variation_mode: str) -> list[str]:
    """Return list of `count` fully-assembled prompts for DALL-E calls."""
    if not 1 <= count <= COUNT_MAX:
        raise ValueError(f"count must be 1–{COUNT_MAX}, got {count}")
    if variation_mode not in VARIATION_TABLES:
        raise ValueError(
            f"variation_mode must be one of {VALID_MODES}, got {variation_mode!r}"
        )
    table = VARIATION_TABLES[variation_mode]
    results = []
    for i in range(count):
        variation = table[i]
        full = (
            f"{STYLE_LOCK}, {prompt}, {variation}, {ANTI_COLLAGE}"
        )
        results.append(full)
    return results
