"""Tests for history thumbnails and batch export."""

from __future__ import annotations

import tempfile
from pathlib import Path

from webapp import export_generated_batch, render_history_page


def test_render_history_page_shows_thumbnail_images():
    html = render_history_page(
        [
            {
                "prompt": "draw a castle",
                "count": 2,
                "output_dir": "output/web",
                "created_at": "2026-05-12T10:00:00Z",
                "images": [
                    {"path": "output/web/generated-01.png", "assistant_message": "done-1"},
                    {"path": "output/web/generated-02.png", "assistant_message": "done-2"},
                ],
            }
        ]
    )

    assert '<img src="/files/output/web/generated-01.png"' in html
    assert '<img src="/files/output/web/generated-02.png"' in html
    assert 'draw a castle' in html


def test_export_generated_batch_copies_all_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src_dir = root / "output/web"
        src_dir.mkdir(parents=True)
        a = src_dir / "generated-01.png"
        b = src_dir / "generated-02.png"
        a.write_bytes(b"a")
        b.write_bytes(b"b")

        destination_dir = root / "exports"
        destination_dir.mkdir()

        exported = export_generated_batch([a, b], destination_dir)

        assert len(exported) == 2
        assert [item.name for item in exported] == ["generated-01.png", "generated-02.png"]
        assert [item.read_bytes() for item in exported] == [b"a", b"b"]


def test_export_generated_batch_deduplicates_collisions():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src_dir = root / "output/web"
        src_dir.mkdir(parents=True)
        a = src_dir / "generated-01.png"
        b = src_dir / "generated-02.png"
        a.write_bytes(b"a")
        b.write_bytes(b"b")

        destination_dir = root / "exports"
        destination_dir.mkdir()
        (destination_dir / "generated-01.png").write_bytes(b"old-a")
        (destination_dir / "generated-02.png").write_bytes(b"old-b")

        exported = export_generated_batch([a, b], destination_dir)

        assert exported[0].name.startswith("generated-01-")
        assert exported[1].name.startswith("generated-02-")
        assert [item.read_bytes() for item in exported] == [b"a", b"b"]
