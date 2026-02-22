import io
import json
import os
import sys
import unittest
from unittest.mock import patch
import urllib.error


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

from metermeter.llm_refiner import LLMRefiner  # noqa: E402
from metermeter.meter_engine import LineAnalysis  # noqa: E402


class _Resp:
    def __init__(self, data: str) -> None:
        self._data = data.encode("utf-8")

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _baseline(line_no: int, text: str) -> LineAnalysis:
    # token_patterns are syllable-level; we keep it simple for testing.
    return LineAnalysis(
        line_no=line_no,
        source_text=text,
        tokens=["To", "strive", "to", "seek"],
        stress_pattern="USUS",
        meter_name="iambic dimeter",
        feet_count=2,
        confidence=0.8,
        oov_tokens=[],
        debug_scores={"iambic:2": 0.8},
        token_patterns=["U", "S", "U", "S"],
    )


def _baseline_multi(line_no: int, text: str) -> LineAnalysis:
    return LineAnalysis(
        line_no=line_no,
        source_text=text,
        tokens=["Sometime", "sun"],
        stress_pattern="USS",
        meter_name="iambic dimeter",
        feet_count=2,
        confidence=0.8,
        oov_tokens=[],
        debug_scores={"iambic:2": 0.8},
        token_patterns=["US", "S"],
    )


class NvimLLMRefinerTests(unittest.TestCase):
    def test_refine_lines_parses_results(self) -> None:
        baselines = [_baseline(10, "To strive to seek")]

        # token_syllables from baseline is [1,1,1,1], so patterns must be 4 tokens of length 1.
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 10,
                        "meter_name": "iambic dimeter",
                        "confidence": 0.92,
                        "analysis_hint": "mock",
                        "token_stress_patterns": ["U", "S", "U", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)

        with patch("urllib.request.urlopen", return_value=_Resp(response)):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            out = ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1)

        self.assertIn(10, out)
        self.assertGreater(out[10].confidence, 0.9)
        self.assertEqual(out[10].token_patterns, ["U", "S", "U", "S"])

    def test_invalid_token_patterns_raise(self) -> None:
        baselines = [_baseline(1, "To strive to seek")]

        # Wrong lengths / bad characters should be rejected and baseline kept.
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 1,
                        "meter_name": "iambic dimeter",
                        "confidence": 0.9,
                        "analysis_hint": "mock",
                        "token_stress_patterns": ["X", "S", "U", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)

        with patch("urllib.request.urlopen", return_value=_Resp(response)):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            with self.assertRaises(RuntimeError):
                ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1)

    def test_parses_fenced_json_content(self) -> None:
        baselines = [_baseline(7, "To strive to seek")]
        content = "```json\n" + json.dumps(
            {
                "results": [
                    {
                        "line_no": 7,
                        "meter_name": "iambic dimeter",
                        "confidence": 0.71,
                        "analysis_hint": "fenced",
                        "token_stress_patterns": ["U", "S", "U", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        ) + "\n```"
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)

        with patch("urllib.request.urlopen", return_value=_Resp(response)):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            out = ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1)

        self.assertIn(7, out)
        self.assertEqual(out[7].analysis_hint, "fenced")

    def test_clamps_confidence_and_trims_hint(self) -> None:
        baselines = [_baseline(5, "To strive to seek")]
        long_hint = "x " * 200
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 5,
                        "meter_name": "iambic dimeter",
                        "confidence": 9.0,
                        "analysis_hint": long_hint,
                        "token_stress_patterns": ["U", "S", "U", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)

        with patch("urllib.request.urlopen", return_value=_Resp(response)):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            out = ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1)

        self.assertIn(5, out)
        self.assertLessEqual(out[5].confidence, 1.0)
        self.assertLessEqual(len(out[5].analysis_hint), 220)

    def test_http_error_raises_runtime_error(self) -> None:
        baselines = [_baseline(3, "To strive to seek")]
        http_err = urllib.error.HTTPError(
            url="http://mock",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            with self.assertRaises(RuntimeError):
                ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1)
        http_err.close()

    def test_meter_name_normalization_from_iambs(self) -> None:
        baselines = [_baseline(9, "To strive to seek")]
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 9,
                        "meter_name": "iambs",
                        "confidence": 0.8,
                        "analysis_hint": "norm",
                        "token_stress_patterns": ["U", "S", "U", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)
        with patch("urllib.request.urlopen", return_value=_Resp(response)):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            out = ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1)
        self.assertIn(9, out)
        self.assertEqual(out[9].meter_name, "iambic dimeter")
        self.assertEqual(out[9].meter_name_raw, "iambs")
        self.assertTrue(out[9].meter_name_normalized)

    def test_repairs_token_pattern_syllable_length_mismatch(self) -> None:
        baselines = [_baseline(12, "To strive to seek")]
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 12,
                        "meter_name": "iambic dimeter",
                        "confidence": 0.8,
                        "analysis_hint": "bad",
                        "token_stress_patterns": ["UU", "S", "U", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)
        with patch("urllib.request.urlopen", return_value=_Resp(response)):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            out = ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1)
        self.assertIn(12, out)
        self.assertEqual(out[12].token_patterns, baselines[0].token_patterns)
        self.assertGreater(out[12].token_repairs_applied, 0)

    def test_strict_mode_rejects_separator_token_patterns(self) -> None:
        baselines = [_baseline_multi(2, "Sometime sun")]
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 2,
                        "meter_name": "iambic dimeter",
                        "confidence": 0.8,
                        "analysis_hint": "strict",
                        "token_stress_patterns": ["U.S", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)
        with patch("urllib.request.urlopen", return_value=_Resp(response)):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            with self.assertRaises(RuntimeError):
                ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1, eval_mode="strict")

    def test_production_mode_repairs_separator_token_patterns(self) -> None:
        baselines = [_baseline_multi(3, "Sometime sun")]
        content = json.dumps(
            {
                "results": [
                    {
                        "line_no": 3,
                        "meter_name": "iambic dimeter",
                        "confidence": 0.8,
                        "analysis_hint": "prod",
                        "token_stress_patterns": ["U.S", "S"],
                    }
                ]
            },
            ensure_ascii=True,
        )
        response = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=True)
        with patch("urllib.request.urlopen", return_value=_Resp(response)):
            ref = LLMRefiner(endpoint="http://mock", model="mock")
            out = ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1, eval_mode="production")
        self.assertIn(3, out)
        self.assertEqual(out[3].token_patterns, ["US", "S"])
        self.assertGreater(out[3].token_repairs_applied, 0)
        self.assertFalse(out[3].strict_eval)
