import json
import unittest

from PoetryMeter.llm_refiner import LLMRefiner
from PoetryMeter.meter_engine import LineAnalysis


def _baseline() -> LineAnalysis:
    return LineAnalysis(
        line_no=1,
        source_text="To strive, to seek, to find, and not to yield",
        tokens=["To", "strive", "to", "seek", "to", "find", "and", "not", "to", "yield"],
        stress_pattern="USUSUSUSUS",
        meter_name="iambic pentameter",
        feet_count=5,
        confidence=0.82,
        oov_tokens=[],
        debug_scores={"iambic:5": 0.82, "trochaic:5": 0.66},
    )


class LLMRefinerTests(unittest.TestCase):
    def test_refine_line_parses_valid_json_payload(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "final_stress_pattern": "USUSUSUSUS",
                                "final_meter": "iambic pentameter",
                                "meter_confidence": 0.91,
                                "analysis_hint": "Try tightening commas to heighten forward momentum.",
                            }
                        )
                    }
                }
            ]
        }

        calls = {"count": 0}

        def transport(_url, _headers, _body, _timeout):
            calls["count"] += 1
            return json.dumps(response)

        refiner = LLMRefiner(
            endpoint="http://127.0.0.1:11434/v1/chat/completions",
            model="local-model",
            transport=transport,
        )

        out = refiner.refine_line(
            line_text=_baseline().source_text,
            baseline=_baseline(),
            timeout_ms=1000,
            temperature=0.1,
        )

        self.assertIsNotNone(out)
        self.assertEqual(out.meter_name, "iambic pentameter")
        self.assertEqual(out.stress_pattern, "USUSUSUSUS")
        self.assertGreater(out.confidence, 0.9)
        self.assertEqual(calls["count"], 1)

    def test_invalid_meter_is_rejected(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "final_stress_pattern": "USUS",
                                "final_meter": "alexandrine",
                                "meter_confidence": 0.7,
                                "analysis_hint": "No hint",
                            }
                        )
                    }
                }
            ]
        }

        def transport(_url, _headers, _body, _timeout):
            return json.dumps(response)

        refiner = LLMRefiner(
            endpoint="http://127.0.0.1:11434/v1/chat/completions",
            model="local-model",
            transport=transport,
        )

        out = refiner.refine_line(
            line_text=_baseline().source_text,
            baseline=_baseline(),
            timeout_ms=1000,
            temperature=0.1,
        )

        self.assertIsNone(out)

    def test_cache_prevents_duplicate_transport_calls(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "final_stress_pattern": "USUSUSUSUS",
                                "final_meter": "iambic pentameter",
                                "meter_confidence": 0.87,
                                "analysis_hint": "Use a harder stop on the final stress.",
                            }
                        )
                    }
                }
            ]
        }

        calls = {"count": 0}

        def transport(_url, _headers, _body, _timeout):
            calls["count"] += 1
            return json.dumps(response)

        refiner = LLMRefiner(
            endpoint="http://127.0.0.1:11434/v1/chat/completions",
            model="local-model",
            transport=transport,
        )

        first = refiner.refine_line(
            line_text=_baseline().source_text,
            baseline=_baseline(),
            timeout_ms=1000,
            temperature=0.1,
        )
        second = refiner.refine_line(
            line_text=_baseline().source_text,
            baseline=_baseline(),
            timeout_ms=1000,
            temperature=0.1,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(calls["count"], 1)


if __name__ == "__main__":
    unittest.main()
