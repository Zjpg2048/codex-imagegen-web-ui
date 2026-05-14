"""Unit tests for prompts.py — no API calls."""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from prompts import (
    ANTI_COLLAGE,
    COUNT_MAX,
    STYLE_LOCK,
    VALID_MODES,
    VARIATION_TABLES,
    build_prompts,
)


class TestBuildPrompts:
    def test_count_matches(self):
        for n in range(1, COUNT_MAX + 1):
            assert len(build_prompts("a cat", n, "pose")) == n

    def test_shared_base_in_all_prompts(self):
        prompts = build_prompts("a warrior", 6, "pose")
        for p in prompts:
            assert STYLE_LOCK in p
            assert "a warrior" in p
            assert ANTI_COLLAGE in p

    def test_variation_words_differ(self):
        prompts = build_prompts("a dog", 6, "pose")
        table = VARIATION_TABLES["pose"]
        for i, p in enumerate(prompts):
            assert table[i] in p

    def test_each_mode_produces_unique_prompts(self):
        for mode in VALID_MODES:
            prompts = build_prompts("a robot", 6, mode)
            assert len(set(prompts)) == 6, f"mode={mode} produced duplicates"

    def test_anti_collage_keywords_present(self):
        keywords = [
            "single subject only",
            "one pose only",
            "not a character sheet",
            "not a collage",
            "not a grid",
            "not a multi-view sheet",
        ]
        prompts = build_prompts("a ninja", 3, "composition")
        for p in prompts:
            for kw in keywords:
                assert kw in p, f"missing keyword {kw!r} in prompt"

    def test_invalid_count_low(self):
        with pytest.raises(ValueError, match="count"):
            build_prompts("x", 0, "pose")

    def test_invalid_count_high(self):
        with pytest.raises(ValueError, match="count"):
            build_prompts("x", 9, "pose")

    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="variation_mode"):
            build_prompts("x", 1, "unknown_mode")

    def test_variation_tables_complete(self):
        for mode in VALID_MODES:
            assert len(VARIATION_TABLES[mode]) >= COUNT_MAX, (
                f"mode={mode} table has fewer than {COUNT_MAX} entries"
            )
