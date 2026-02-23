import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

import metermeter_cli  # noqa: E402


class _Resp:
    def __init__(self, data: str) -> None:
        self._data = data.encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class NvimCLILLMTests(unittest.TestCase):
    def test_cli_marks_refined_lines_as_llm(self) -> None:
        req = {
            "config": {
                "llm": {
                    "enabled": True,
                    "endpoint": "http://mock",
                    "model": "mock-model",
                    "timeout_ms": 1000,
                    "temperature": 0.1,
                    "max_lines_per_scan": 2,
                },
                "lexicon_path": "",
            },
            "lines": [
                {"lnum": 0, "text": "The trampled fruit yields wine that's sweet and red."},
                {"lnum": 1, "text": "And plants will dream, thy flax to fit a nuptial bed."},
            ],
        }

        # Build a strict-valid refinement response for both lines.
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 0,
                        "meter_name": "iambic pentameter",
                        "confidence": 0.9,

                        "token_stress_patterns": ["U", "SU", "S", "U", "S", "U", "S", "U", "S"],
                    },
                    {
                        "line_no": 1,
                        "meter_name": "iambic pentameter",
                        "confidence": 0.9,

                        "token_stress_patterns": ["U", "S", "U", "S", "U", "S", "U", "S", "U", "SU", "S"],
                    },
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)

        stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout), patch("urllib.request.urlopen", return_value=_Resp(response)):
            rc = metermeter_cli.main()
        self.assertEqual(rc, 0)

        out = json.loads(stdout.getvalue() or "{}")
        results = out.get("results") or []
        self.assertEqual(len(results), 2)
        eval_obj = out.get("eval") or {}
        self.assertEqual(eval_obj.get("mode"), "production")
        self.assertEqual(eval_obj.get("result_count"), 2)

    def test_cli_errors_when_llm_disabled(self) -> None:
        req = {
            "config": {"llm": {"enabled": False}, "lexicon_path": ""},
            "lines": [
                {"lnum": 0, "text": "The trampled fruit yields wine that's sweet and red."},
                {"lnum": 1, "text": "And plants will dream, thy flax to fit a nuptial bed."},
            ],
        }

        stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            rc = metermeter_cli.main()
        self.assertEqual(rc, 0)

        out = json.loads(stdout.getvalue() or "{}")
        results = out.get("results") or []
        self.assertEqual(len(results), 0)
        self.assertEqual(out.get("error"), "llm_disabled")
        self.assertEqual((out.get("eval") or {}).get("mode"), "production")

    def test_cli_ignores_invalid_line_entries(self) -> None:
        req = {
            "config": {
                "llm": {"enabled": True, "endpoint": "mock://llm", "model": "mock", "max_lines_per_scan": 16},
                "lexicon_path": "",
            },
            "lines": [
                {"lnum": 0, "text": "Valid line one."},
                {"text": "missing line number"},
                {"lnum": "2", "text": "bad lnum type"},
                {"lnum": 3, "text": 123},
                "not-a-dict",
                {"lnum": 4, "text": "Valid line two."},
            ],
        }

        stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            rc = metermeter_cli.main()
        self.assertEqual(rc, 0)

        out = json.loads(stdout.getvalue() or "{}")
        results = out.get("results") or []
        self.assertEqual([r.get("lnum") for r in results], [0, 4])
        self.assertEqual((out.get("eval") or {}).get("result_count"), 2)

    def test_cli_strict_eval_mode_reports_strict(self) -> None:
        req = {
            "config": {
                "llm": {
                    "enabled": True,
                    "endpoint": "mock://llm",
                    "model": "mock",
                    "max_lines_per_scan": 16,
                    "eval_mode": "strict",
                },
                "lexicon_path": "",
            },
            "lines": [
                {"lnum": 0, "text": "Valid line one."},
            ],
        }

        stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            rc = metermeter_cli.main()
        self.assertEqual(rc, 0)
        out = json.loads(stdout.getvalue() or "{}")
        eval_obj = out.get("eval") or {}
        self.assertEqual(eval_obj.get("mode"), "strict")
        self.assertTrue(eval_obj.get("strict"))

    def test_cli_rescores_meter_name_from_token_pattern(self) -> None:
        req = {
            "config": {
                "llm": {
                    "enabled": True,
                    "endpoint": "http://mock",
                    "model": "mock-model",
                    "timeout_ms": 1000,
                    "temperature": 0.1,
                    "max_lines_per_scan": 4,
                },
                "context": {
                    "dominant_meter": "iambic pentameter",
                    "dominant_ratio": 0.85,
                    "dominant_line_count": 12,
                },
            },
            "lines": [
                {"lnum": 0, "text": "The trampled fruit yields wine that's sweet and red."},
            ],
        }
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 0,
                        "meter_name": "trochaic tetrameter",
                        "confidence": 0.60,

                        "token_stress_patterns": ["U", "SU", "S", "U", "S", "U", "S", "U", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)

        stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout), patch("urllib.request.urlopen", return_value=_Resp(response)):
            rc = metermeter_cli.main()
        self.assertEqual(rc, 0)

        out = json.loads(stdout.getvalue() or "{}")
        results = out.get("results") or []
        self.assertEqual(len(results), 1)
        row = results[0]
        self.assertEqual(row.get("meter_name_llm"), "trochaic tetrameter")
        self.assertEqual(row.get("meter_name"), "iambic pentameter")
        self.assertTrue(row.get("meter_overridden"))
        self.assertEqual(row.get("override_reason"), "pattern_rescore")
        self.assertEqual((out.get("eval") or {}).get("meter_overrides"), 1)

    def test_resolve_path_explicit_path(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as fh:
            fh.write(b"{}")
            path = fh.name
        try:
            result = metermeter_cli._resolve_path(path, "UNUSED_ENV_VAR", [])
            self.assertEqual(result, path)
        finally:
            os.unlink(path)

    def test_resolve_path_env_fallback(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as fh:
            fh.write(b"{}")
            path = fh.name
        try:
            with patch.dict(os.environ, {"TEST_RESOLVE_PATH_ENV": path}):
                result = metermeter_cli._resolve_path("", "TEST_RESOLVE_PATH_ENV", [])
            self.assertEqual(result, path)
        finally:
            os.unlink(path)

    def test_resolve_path_home_fallback(self) -> None:
        home = os.path.expanduser("~")
        mm_dir = os.path.join(home, ".metermeter")
        os.makedirs(mm_dir, exist_ok=True)
        sentinel = os.path.join(mm_dir, "_test_resolve_sentinel.json")
        try:
            with open(sentinel, "w") as f:
                f.write("{}")
            result = metermeter_cli._resolve_path("", "NONEXISTENT_ENV_VAR_1234", ["_test_resolve_sentinel.json"])
            self.assertEqual(result, sentinel)
        finally:
            if os.path.exists(sentinel):
                os.unlink(sentinel)

    def test_resolve_path_returns_original_when_nothing_found(self) -> None:
        result = metermeter_cli._resolve_path("~/nonexistent_path_xyz", "NONEXISTENT_ENV_VAR_1234", ["no_such_file.json"])
        self.assertEqual(result, os.path.expanduser("~/nonexistent_path_xyz"))
