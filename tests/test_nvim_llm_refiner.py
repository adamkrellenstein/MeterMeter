import io
import json
import os
import sys
import unittest
from unittest.mock import patch


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "poetrymeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

from poetrymeter.llm_refiner import LLMRefiner  # noqa: E402
from poetrymeter.meter_engine import LineAnalysis  # noqa: E402


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

    def test_invalid_token_patterns_fall_back_to_baseline(self) -> None:
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
            out = ref.refine_lines(baselines, timeout_ms=1000, temperature=0.1)

        self.assertIn(1, out)
        self.assertEqual(out[1].token_patterns, baselines[0].token_patterns)

