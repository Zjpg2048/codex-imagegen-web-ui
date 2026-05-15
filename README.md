# comic

`comic` is a local image and image-to-video playground with two workflows:

1. **Recommended:** a web app that uses `codex exec` for image generation and a local Remotion project for video rendering.
2. **Legacy fallback:** a direct Python CLI that calls OpenAI with `OPENAI_API_KEY`.

## Recommended workflow: Web app

### Requirements

- Python 3.10+
- `codex` installed and authenticated
- Node.js + npm for local Remotion video rendering

If needed:

```bash
codex login
```

### Start the web app

```bash
scripts/start_web.sh --port 8000
```

Or:

```bash
python3 webapp.py --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

### Install local video-rendering dependencies

Image-to-video rendering uses the fixed local project in `remotion-video/`.

```bash
cd remotion-video
npm install
```

If these dependencies are missing, the app will reject video render requests with a clear error.

### Features

#### Image generation

- Prompt input
- Image count selection (`1-8`)
- Project-local output directory selection
- Background tasks with progress polling
- Reference image upload
- History page at `/history`
- Task page at `/tasks`
- Per-image export
- Batch export

#### Image-to-video

- Upload a source image
- Or reuse a previously generated project image
- Motion prompt input
- Duration control
- Aspect ratio control (`16:9`, `9:16`, `1:1`)
- Fixed local Remotion template render to MP4
- Video task tracking and export

#### Image to text

- Upload an image and extract a reusable generation prompt or structured visual fields
- **Agent selection:** Claude (default, uses local `claude` CLI with Haiku 4.5) or Codex
- Two analysis modes: Reverse prompt / Structured analysis
- Optional instruction to focus the analysis
- Result displayed inline within the same section
- "Use as image prompt" button to feed result directly into the generation form

### How it works

#### Images

- The browser submits the prompt to the local backend.
- The backend creates a background task immediately.
- The task runs `codex exec`.
- Codex is instructed to use its built-in `imagegen` tool.
- Generated images are copied into your chosen output directory.

#### Videos

- The backend validates the source image.
- The source image is staged into `remotion-video/public/input/`.
- Render props are written to a project-local JSON file.
- The backend runs the fixed local Remotion render command.
- The final MP4 is written into your chosen output directory.

### Notes

- The web app does **not** require `OPENAI_API_KEY` for image generation.
- It **does** require an authenticated local Codex session for image generation and Codex-based image analysis.
- Image-to-text with Claude agent requires a logged-in local `claude` CLI session (`claude login`).
- Video rendering depends on the local `remotion-video/` workspace.
- Output directories must stay inside this project.
- Export destinations may be outside the project, but they must already exist and be directories.

## Legacy workflow: Direct Python CLI

Use this only if you explicitly want direct API usage.

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Start

```bash
export OPENAI_API_KEY=your_key_here
scripts/start.sh --prompt "A cyberpunk girl portrait" --count 3
```

Or:

```bash
python3 generate.py --prompt "A cyberpunk girl portrait" --count 3
```

### Example

```bash
scripts/start.sh \
  --prompt "A cyberpunk girl portrait" \
  --count 3 \
  --variation-mode pose \
  --out-dir output \
  --size 1024x1024 \
  --quality standard
```

## Testing

```bash
pytest -q
```
