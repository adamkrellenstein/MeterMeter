import os
import sys
import unittest


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

from metermeter.meter_engine import MeterEngine  # noqa: E402


class MeterRescoringTests(unittest.TestCase):
    def test_best_meter_for_iambic_pattern(self) -> None:
        engine = MeterEngine()
        meter, score, debug = engine.best_meter_for_stress_pattern("USUSUSUSUS")
        self.assertEqual(meter, "iambic pentameter")
        self.assertGreaterEqual(score, 0.80)
        self.assertGreaterEqual(float(debug.get("margin") or 0.0), 0.05)

    def test_best_meter_for_trochaic_pattern(self) -> None:
        engine = MeterEngine()
        meter, score, debug = engine.best_meter_for_stress_pattern("SUSUSUSU")
        self.assertEqual(meter, "trochaic tetrameter")
        self.assertGreaterEqual(score, 0.75)
        self.assertGreaterEqual(float(debug.get("margin") or 0.0), 0.03)

    def test_best_meter_for_anapestic_pattern(self) -> None:
        engine = MeterEngine()
        meter, score, debug = engine.best_meter_for_stress_pattern("UUSUUSUUSUUS")
        self.assertEqual(meter, "anapestic tetrameter")
        self.assertGreaterEqual(score, 0.72)
        self.assertGreaterEqual(float(debug.get("margin") or 0.0), 0.03)

    def test_best_meter_for_dactylic_pattern(self) -> None:
        engine = MeterEngine()
        meter, score, debug = engine.best_meter_for_stress_pattern("SUUSUUSUUSUUSUUSUUS")
        self.assertEqual(meter, "dactylic hexameter")
        self.assertGreaterEqual(score, 0.72)
        self.assertGreaterEqual(float(debug.get("margin") or 0.0), 0.03)

    def test_score_stress_pattern_for_specific_meter(self) -> None:
        engine = MeterEngine()
        iambic = engine.score_stress_pattern_for_meter("USUSUSUSUS", "iambic pentameter")
        trochaic = engine.score_stress_pattern_for_meter("USUSUSUSUS", "trochaic pentameter")
        self.assertIsNotNone(iambic)
        self.assertIsNotNone(trochaic)
        assert iambic is not None and trochaic is not None
        self.assertGreater(iambic, trochaic)

    def test_invalid_meter_name_returns_none(self) -> None:
        engine = MeterEngine()
        self.assertIsNone(engine.score_stress_pattern_for_meter("USUS", "invalid meter"))
