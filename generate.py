#!/usr/bin/env python3
"""CLI: generate N independent images from a single user intent."""

import argparse
import json
import sys

from prompts import COUNT_MAX, VALID_MODES, build_prompts
from generator import run_generation_batch


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate multiple independent images from one prompt."
    )
    parser.add_argument("--prompt", required=True, help="User intent description")
    parser.add_argument(
        "--count",
        type=int,
        required=True,
        help=f"Number of images to generate (1–{COUNT_MAX})",
    )
    parser.add_argument(
        "--variation-mode",
        default="pose",
        choices=VALID_MODES,
        dest="variation_mode",
        help="Dimension along which images vary (default: pose)",
    )
    parser.add_argument(
        "--out-dir",
        default="output",
        dest="out_dir",
        help="Directory to save generated images (default: ./output)",
    )
    parser.add_argument(
        "--size",
        default="1024x1024",
        choices=["1024x1024", "1792x1024", "1024x1792"],
        help="Image size (default: 1024x1024)",
    )
    parser.add_argument(
        "--quality",
        default="standard",
        choices=["standard", "hd"],
        help="DALL-E 3 quality (default: standard)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.prompt.strip():
        print("Error: --prompt must not be empty", file=sys.stderr)
        return 1

    if not (1 <= args.count <= COUNT_MAX):
        print(
            f"Error: --count must be between 1 and {COUNT_MAX}, got {args.count}",
            file=sys.stderr,
        )
        return 1

    print(f"Building {args.count} prompts (mode={args.variation_mode}) …")
    sub_prompts = build_prompts(args.prompt, args.count, args.variation_mode)

    for i, p in enumerate(sub_prompts, 1):
        print(f"  [{i}] {p}")

    print(f"\nGenerating {args.count} images → {args.out_dir}/")
    results = run_generation_batch(
        sub_prompts,
        out_dir=args.out_dir,
        size=args.size,
        quality=args.quality,
    )

    ok = [r for r in results if r.status == "ok"]
    errors = [r for r in results if r.status == "error"]

    print(f"\nDone: {len(ok)} succeeded, {len(errors)} failed")
    for r in results:
        if r.status == "ok":
            print(f"  [{r.index}] OK  → {r.file_path}")
        else:
            print(f"  [{r.index}] ERR → {r.error}")

    # machine-readable summary
    summary = [
        {
            "index": r.index,
            "status": r.status,
            "file_path": r.file_path,
            "error": r.error,
            "final_prompt": r.prompt,
        }
        for r in results
    ]
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if errors == [] else 2


if __name__ == "__main__":
    sys.exit(main())
