import json
import unittest

from llm_refiner import LLMRefiner
from meter_engine import LineAnalysis


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
        token_patterns=["U", "S", "U", "S", "U", "S", "U", "S", "U", "S"],
    )


class LLMRefinerTests(unittest.TestCase):
    def test_v2_payload_includes_line_level_constraints(self) -> None:
        refiner = LLMRefiner(
            endpoint="http://127.0.0.1:11434/v1/chat/completions",
            model="local-model",
            prompt_version="v2",
            transport=lambda _url, _headers, _body, _timeout: "",
        )

        payload = refiner._build_payload(_baseline().source_text, _baseline(), 0.1)
        self.assertIn("messages", payload)
        messages = payload["messages"]
        self.assertIsInstance(messages, list)
        self.assertIn("line level", messages[0]["content"].lower())
        user_payload = json.loads(messages[1]["content"])
        self.assertIn("tokens", user_payload)
        self.assertIn("token_syllables", user_payload)

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

    def test_missing_token_patterns_falls_back_to_baseline_split(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "final_stress_pattern": "USUSUSUSUS",
                                "final_meter": "iambic pentameter",
                                "meter_confidence": 0.88,
                                "analysis_hint": "Keep the unstressed pickups lighter.",
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

        self.assertIsNotNone(out)
        self.assertEqual(out.token_patterns, _baseline().token_patterns)

    def test_token_patterns_override_inconsistent_final_stress(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "final_stress_pattern": "SSSSSSSSSS",
                                "final_meter": "iambic pentameter",
                                "meter_confidence": 0.88,
                                "analysis_hint": "Keep the unstressed pickups lighter.",
                                "token_stress_patterns": ["U", "S", "U", "S", "U", "S", "U", "S", "U", "S"],
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

        self.assertIsNotNone(out)
        self.assertEqual(out.stress_pattern, "USUSUSUSUS")

    def test_refine_lines_batches_and_parses(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "results": [
                                    {
                                        "line_no": 1,
                                        "final_stress_pattern": "USUSUSUSUS",
                                        "final_meter": "iambic pentameter",
                                        "meter_confidence": 0.9,
                                        "analysis_hint": "Keep the line-level rhythm consistent across phrases.",
                                        "token_stress_patterns": _baseline().token_patterns,
                                    }
                                ]
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

        out = refiner.refine_lines([_baseline()], timeout_ms=1000, temperature=0.1)
        self.assertIn(1, out)
        self.assertEqual(out[1].meter_name, "iambic pentameter")
        self.assertEqual(out[1].stress_pattern, "USUSUSUSUS")
        self.assertEqual(calls["count"], 1)

if __name__ == "__main__":
    unittest.main()
