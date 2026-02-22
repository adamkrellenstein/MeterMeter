import os
import sys
import unittest


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

from metermeter.meter_engine import MeterEngine  # noqa: E402


SONNET_18_GOLD = [
    {"line_no": 1, "text": "Shall I compare thee to a summer's day?", "expected_meter": "iambic pentameter"},
    {"line_no": 2, "text": "Thou art more lovely and more temperate:", "expected_meter": "iambic pentameter"},
    {
        "line_no": 3,
        "text": "Rough winds do shake the darling buds of May,",
        "expected_meter": "iambic pentameter",
        "substitution": "opening spondee",
    },
    {"line_no": 4, "text": "And summer's lease hath all too short a date:", "expected_meter": "iambic pentameter"},
    {
        "line_no": 5,
        "text": "Sometime too hot the eye of heaven shines,",
        "expected_meter": "iambic pentameter",
        "substitution": "opening trochee",
    },
    {"line_no": 6, "text": "And often is his gold complexion dimmed;", "expected_meter": "iambic pentameter"},
    {"line_no": 7, "text": "And every fair from fair sometime declines,", "expected_meter": "iambic pentameter"},
    {"line_no": 8, "text": "By chance or nature's changing course untrimmed:", "expected_meter": "iambic pentameter"},
    {"line_no": 9, "text": "But thy eternal summer shall not fade,", "expected_meter": "iambic pentameter"},
    {"line_no": 10, "text": "Nor lose possession of that fair thou ow'st;", "expected_meter": "iambic pentameter"},
    {
        "line_no": 11,
        "text": "Nor shall Death brag thou wander'st in his shade,",
        "expected_meter": "iambic pentameter",
        "substitution": "second-foot spondee",
    },
    {"line_no": 12, "text": "When in eternal lines to time thou grow'st:", "expected_meter": "iambic pentameter"},
    {"line_no": 13, "text": "So long as men can breathe or eyes can see,", "expected_meter": "iambic pentameter"},
    {"line_no": 14, "text": "So long lives this and this gives life to thee.", "expected_meter": "iambic pentameter"},
]


class Sonnet18AccuracyTests(unittest.TestCase):
    def test_gold_fixture_shape(self) -> None:
        self.assertEqual(len(SONNET_18_GOLD), 14)
        for row in SONNET_18_GOLD:
            self.assertIn("text", row)
            self.assertIn("expected_meter", row)

    def test_engine_primary_meter_baseline_floor(self) -> None:
        # Accuracy guardrail for iterative improvement on a canonical iambic pentameter poem.
        engine = MeterEngine()
        matches = 0
        total = len(SONNET_18_GOLD)
        mismatches = []
        for row in SONNET_18_GOLD:
            got = engine.analyze_line(row["text"], line_no=row["line_no"])
            self.assertIsNotNone(got, f"line {row['line_no']} returned no analysis")
            assert got is not None
            if got.meter_name == row["expected_meter"]:
                matches += 1
            else:
                mismatches.append(f"{row['line_no']}: {got.meter_name} != {row['expected_meter']}")

        accuracy = matches / float(total)
        self.assertGreaterEqual(
            accuracy,
            0.70,
            "sonnet18 accuracy regression: {:.1%}\n{}".format(accuracy, "\n".join(mismatches)),
        )
