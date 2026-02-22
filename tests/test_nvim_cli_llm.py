import io
import json
import os
import sys
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
                }
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
                        "analysis_hint": "mock",
                        "token_stress_patterns": ["U", "SU", "S", "U", "S", "U", "S", "U", "S"],
                    },
                    {
                        "line_no": 1,
                        "meter_name": "iambic pentameter",
                        "confidence": 0.9,
                        "analysis_hint": "mock",
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
        self.assertTrue(all(r.get("source") == "llm" for r in results), results)
        eval_obj = out.get("eval") or {}
        self.assertEqual(eval_obj.get("mode"), "production")
        self.assertEqual(eval_obj.get("result_count"), 2)

    def test_cli_errors_when_llm_disabled(self) -> None:
        req = {
            "config": {"llm": {"enabled": False}},
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
            "config": {"llm": {"enabled": True, "endpoint": "mock://llm", "model": "mock", "max_lines_per_scan": 16}},
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
                }
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
