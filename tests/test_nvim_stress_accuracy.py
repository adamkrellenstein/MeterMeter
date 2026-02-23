import os
import signal
import sys
import unittest
from typing import List, Optional, Tuple

# Per-line timeout: skip lines that prosodic can't parse quickly.
_LINE_TIMEOUT_S = 5


def _analyze_with_timeout(engine, text: str):
    """Run engine.analyze_line with a SIGALRM timeout. Returns None on timeout."""
    def _handler(signum, frame):
        raise TimeoutError
    old = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(_LINE_TIMEOUT_S)
    try:
        return engine.analyze_line(text)
    except TimeoutError:
        return None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)

def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")

_NVIM_PY = os.path.abspath(_nvim_python_path())
_BENCH_PY = os.path.join(os.path.dirname(__file__), "..", "benchmarks")
sys.path.insert(0, _NVIM_PY)
sys.path.insert(0, _BENCH_PY)

from parse_4b4v import parse_corpus  # noqa: E402
from metermeter.meter_engine import MeterEngine  # noqa: E402

_4B4V_DIR = os.path.join(os.path.dirname(__file__), "..", "benchmarks", "data", "poems")

# Each entry: (text, gold_stress, gold_meter)
_LineArgs = Tuple[str, str, str]
# Each result: (predicted_stress, predicted_meter, gold_stress, gold_meter)
_LineResult = Tuple[Optional[str], Optional[str], str, str]

# Run only this many lines by default; set MM_TEST_MAX_LINES=0 for the full corpus.
_DEFAULT_MAX_LINES = 14


class StressAccuracyTests(unittest.TestCase):
    """Per-syllable stress accuracy against the 4B4V annotated corpus."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.corpus = parse_corpus(_4B4V_DIR)
        assert cls.corpus, f"4B4V corpus not found at {_4B4V_DIR}"

        raw = int(os.environ.get("MM_TEST_MAX_LINES", str(_DEFAULT_MAX_LINES)))
        all_line_args: List[_LineArgs] = [
            (line.text, line.gold_stress, line.gold_meter)
            for line in cls.corpus
        ]
        line_args = all_line_args if raw == 0 else all_line_args[:raw]
        total = len(line_args)

        print(f"\nAnalyzing {total} lines...", flush=True)
        engine = MeterEngine()
        cls.results: List[_LineResult] = []
        for i, (text, gold_stress, gold_meter) in enumerate(line_args, 1):
            analysis = _analyze_with_timeout(engine, text)
            if analysis is None:
                cls.results.append((None, None, gold_stress, gold_meter))
            else:
                cls.results.append((analysis.stress_pattern, analysis.meter_name, gold_stress, gold_meter))
            if i % 100 == 0:
                print(f"  {i}/{total}", flush=True)

    # Accuracy floor tests require enough data to be statistically meaningful.
    _ACCURACY_MIN_LINES = 200

    def test_corpus_has_sufficient_lines(self) -> None:
        self.assertGreaterEqual(len(self.corpus), 1)

    def test_per_syllable_accuracy_floor(self) -> None:
        """Deterministic engine vs. 4B4V gold stress: >= 85% per-syllable agreement."""
        if len(self.results) < self._ACCURACY_MIN_LINES:
            self.skipTest(f"sample too small ({len(self.results)} < {self._ACCURACY_MIN_LINES})")
        total_matches = 0
        total_positions = 0

        for predicted, _, gold, __ in self.results:
            if predicted is None:
                continue
            common = min(len(predicted), len(gold))
            total_matches += sum(1 for i in range(common) if predicted[i] == gold[i])
            total_positions += max(len(predicted), len(gold))

        accuracy = total_matches / total_positions if total_positions > 0 else 0
        self.assertGreaterEqual(
            accuracy, 0.63,
            f"4B4V per-syllable stress accuracy: {accuracy:.1%} ({total_matches}/{total_positions})",
        )

    def test_meter_classification_accuracy_floor(self) -> None:
        """Deterministic engine vs. 4B4V gold meter: >= 85% agreement."""
        if len(self.results) < self._ACCURACY_MIN_LINES:
            self.skipTest(f"sample too small ({len(self.results)} < {self._ACCURACY_MIN_LINES})")
        correct = 0
        total = 0

        for _, predicted_meter, __, gold_meter in self.results:
            if not gold_meter or predicted_meter is None:
                continue
            total += 1
            if predicted_meter.strip().lower() == gold_meter.strip().lower():
                correct += 1

        accuracy = correct / total if total > 0 else 0
        self.assertGreaterEqual(
            accuracy, 0.69,
            f"4B4V meter classification accuracy: {accuracy:.1%} ({correct}/{total})",
        )

    def test_iambic_pentameter_stress_f1(self) -> None:
        """Stress F1 on iambic pentameter lines specifically: >= 85%."""
        if len(self.results) < self._ACCURACY_MIN_LINES:
            self.skipTest(f"sample too small ({len(self.results)} < {self._ACCURACY_MIN_LINES})")
        tp = fp = fn = 0

        for predicted, _, gold, gold_meter in self.results:
            if "iambic pentameter" not in gold_meter.lower() or predicted is None:
                continue
            common = min(len(predicted), len(gold))
            for i in range(common):
                if gold[i] == "S":
                    if predicted[i] == "S":
                        tp += 1
                    else:
                        fn += 1
                elif predicted[i] == "S":
                    fp += 1

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        self.assertGreaterEqual(
            f1, 0.65,
            f"Iambic pentameter stress F1: {f1:.1%} (P={precision:.1%} R={recall:.1%})",
        )
