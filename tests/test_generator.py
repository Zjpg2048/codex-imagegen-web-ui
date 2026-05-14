"""Unit tests for generator.py — all API calls are mocked."""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from generator import ImageResult, run_generation_batch


def _make_client(url: str = "https://example.com/fake.png") -> MagicMock:
    """Return a mock openai.OpenAI client that returns a fixed image URL."""
    client = MagicMock()
    img = MagicMock()
    img.url = url
    client.images.generate.return_value.data = [img]
    return client


class TestRunGenerationBatch:
    def test_calls_api_n_times(self):
        client = _make_client()
        prompts = ["p1", "p2", "p3"]
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("urllib.request.urlretrieve"):
                results = run_generation_batch(prompts, out_dir, client=client)
        assert client.images.generate.call_count == 3

    def test_each_call_uses_n1(self):
        client = _make_client()
        prompts = ["a", "b"]
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("urllib.request.urlretrieve"):
                run_generation_batch(prompts, out_dir, client=client)
        for c in client.images.generate.call_args_list:
            assert c.kwargs.get("n", c.args[1] if len(c.args) > 1 else None) == 1 or \
                   c.kwargs["n"] == 1

    def test_results_have_unique_file_paths(self):
        client = _make_client()
        prompts = ["a", "b", "c", "d", "e", "f"]
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("urllib.request.urlretrieve"):
                results = run_generation_batch(prompts, out_dir, client=client)
        paths = [r.file_path for r in results if r.status == "ok"]
        assert len(paths) == len(set(paths)) == 6

    def test_result_count_matches_prompt_count(self):
        client = _make_client()
        prompts = ["x"] * 4
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("urllib.request.urlretrieve"):
                results = run_generation_batch(prompts, out_dir, client=client)
        assert len(results) == 4

    def test_result_structure(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("urllib.request.urlretrieve"):
                results = run_generation_batch(["hello"], out_dir, client=client)
        r = results[0]
        assert r.index == 1
        assert r.prompt == "hello"
        assert r.status == "ok"
        assert r.file_path != ""
        assert r.error == ""

    def test_single_failure_does_not_abort(self):
        """One API error → that slot is error, rest succeed."""
        client = MagicMock()
        img = MagicMock()
        img.url = "https://example.com/ok.png"

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise RuntimeError("API timeout")
            resp = MagicMock()
            resp.data = [img]
            return resp

        client.images.generate.side_effect = side_effect

        prompts = ["p1", "p2", "p3", "p4", "p5"]
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("urllib.request.urlretrieve"):
                results = run_generation_batch(prompts, out_dir, client=client)

        assert len(results) == 5
        assert results[2].status == "error"
        assert "API timeout" in results[2].error
        ok_results = [r for r in results if r.status == "ok"]
        assert len(ok_results) == 4

    def test_error_result_has_no_file_path(self):
        client = MagicMock()
        client.images.generate.side_effect = RuntimeError("fail")
        with tempfile.TemporaryDirectory() as out_dir:
            results = run_generation_batch(["p"], out_dir, client=client)
        assert results[0].status == "error"
        assert results[0].file_path == ""

    def test_indices_are_1_based(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as out_dir:
            with patch("urllib.request.urlretrieve"):
                results = run_generation_batch(["a", "b", "c"], out_dir, client=client)
        assert [r.index for r in results] == [1, 2, 3]

    def test_out_dir_created_if_missing(self):
        client = _make_client()
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "new_subdir")
            assert not os.path.exists(out_dir)
            with patch("urllib.request.urlretrieve"):
                run_generation_batch(["p"], out_dir, client=client)
            assert os.path.isdir(out_dir)


class TestCliArgParsing:
    def test_missing_prompt_exits(self):
        from generate import parse_args
        with pytest.raises(SystemExit):
            parse_args(["--count", "3"])

    def test_missing_count_exits(self):
        from generate import parse_args
        with pytest.raises(SystemExit):
            parse_args(["--prompt", "test"])

    def test_invalid_mode_exits(self):
        from generate import parse_args
        with pytest.raises(SystemExit):
            parse_args(["--prompt", "x", "--count", "3", "--variation-mode", "bad"])

    def test_defaults(self):
        from generate import parse_args
        args = parse_args(["--prompt", "x", "--count", "2"])
        assert args.variation_mode == "pose"
        assert args.out_dir == "output"
        assert args.size == "1024x1024"
        assert args.quality == "standard"

    def test_count_too_low_returns_error_code(self):
        from generate import main
        rc = main(["--prompt", "x", "--count", "0"])
        assert rc == 1

    def test_count_too_high_returns_error_code(self):
        from generate import main
        rc = main(["--prompt", "x", "--count", "9"])
        assert rc == 1

    def test_blank_prompt_returns_error_code(self):
        from generate import main
        rc = main(["--prompt", "   ", "--count", "1"])
        assert rc == 1


class TestCliOutputContract:
    def test_summary_uses_final_prompt_key(self, capsys):
        from generate import main

        with patch("generate.run_generation_batch") as mock_batch:
            mock_batch.return_value = [
                ImageResult(
                    index=1,
                    prompt="assembled prompt",
                    status="ok",
                    file_path="output/image_01.png",
                )
            ]

            rc = main(["--prompt", "x", "--count", "1"])

        captured = capsys.readouterr()
        assert rc == 0
        assert '"final_prompt": "assembled prompt"' in captured.out
