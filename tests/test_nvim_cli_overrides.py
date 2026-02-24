import os
import sys
import unittest

def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")

sys.path.insert(0, os.path.abspath(_nvim_python_path()))

import metermeter_cli  # noqa: E402
from metermeter.meter_engine import MeterEngine  # noqa: E402


class PatternRescoreTests(unittest.TestCase):
    def test_rescore_overrides_when_score_and_margin_high(self) -> None:
        result = metermeter_cli._try_pattern_rescore(
            meter_name="trochaic tetrameter", conf=0.6,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.85, pattern_best_margin=0.15,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "iambic pentameter")
        self.assertEqual(result[2], "pattern_rescore")

    def test_rescore_no_override_when_same_meter(self) -> None:
        result = metermeter_cli._try_pattern_rescore(
            meter_name="iambic pentameter", conf=0.6,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.90, pattern_best_margin=0.20,
        )
        self.assertIsNone(result)

    def test_rescore_no_override_when_score_below_threshold(self) -> None:
        result = metermeter_cli._try_pattern_rescore(
            meter_name="trochaic tetrameter", conf=0.6,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.60,  # Below RESCORE_MIN_SCORE=0.72
            pattern_best_margin=0.15,
        )
        self.assertIsNone(result)

    def test_rescore_no_override_when_margin_below_threshold(self) -> None:
        result = metermeter_cli._try_pattern_rescore(
            meter_name="trochaic tetrameter", conf=0.6,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.80,
            pattern_best_margin=0.05,  # Below RESCORE_MIN_MARGIN=0.10
        )
        self.assertIsNone(result)

    def test_rescore_confidence_capped(self) -> None:
        result = metermeter_cli._try_pattern_rescore(
            meter_name="trochaic tetrameter", conf=0.50,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.85, pattern_best_margin=0.15,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[1], 0.50)  # min(conf, pattern_best_score)

    def test_rescore_boundary_exact_thresholds(self) -> None:
        # Exactly at thresholds should pass
        result = metermeter_cli._try_pattern_rescore(
            meter_name="trochaic tetrameter", conf=0.6,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=metermeter_cli.RESCORE_MIN_SCORE,
            pattern_best_margin=metermeter_cli.RESCORE_MIN_MARGIN,
        )
        self.assertIsNotNone(result)


class IambicGuardTests(unittest.TestCase):
    def test_guard_overrides_non_iambic_with_matching_stress(self) -> None:
        result = metermeter_cli._try_iambic_guard(
            meter_name="trochaic pentameter", conf=0.60,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.75, pattern_best_margin=0.05,
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "iambic pentameter")
        self.assertEqual(result[2], "iambic_guard")

    def test_guard_no_override_when_already_iambic(self) -> None:
        result = metermeter_cli._try_iambic_guard(
            meter_name="iambic pentameter", conf=0.60,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.75, pattern_best_margin=0.05,
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNone(result)

    def test_guard_no_override_when_confidence_too_high(self) -> None:
        result = metermeter_cli._try_iambic_guard(
            meter_name="trochaic pentameter", conf=0.80,  # Above IAMBIC_GUARD_MAX_CONF=0.75
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.75, pattern_best_margin=0.05,
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNone(result)

    def test_guard_no_override_when_pattern_not_iambic(self) -> None:
        result = metermeter_cli._try_iambic_guard(
            meter_name="trochaic tetrameter", conf=0.60,
            pattern_best_meter="trochaic tetrameter",  # Pattern agrees with LLM
            pattern_best_score=0.75, pattern_best_margin=0.05,
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNone(result)

    def test_guard_no_override_when_not_trochaic(self) -> None:
        result = metermeter_cli._try_iambic_guard(
            meter_name="anapestic trimeter", conf=0.60,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.75, pattern_best_margin=0.05,
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNone(result)

    def test_guard_fires_for_tetrameter(self) -> None:
        result = metermeter_cli._try_iambic_guard(
            meter_name="trochaic tetrameter", conf=0.60,
            pattern_best_meter="iambic tetrameter",
            pattern_best_score=0.75, pattern_best_margin=0.05,
            stress_pattern="USUSUSUS",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "iambic tetrameter")
        self.assertEqual(result[2], "iambic_guard")

    def test_guard_suppressed_with_precomputed_context(self) -> None:
        result = metermeter_cli._try_iambic_guard(
            meter_name="trochaic pentameter", conf=0.60,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.75, pattern_best_margin=0.05,
            stress_pattern="USUSUSUSUS",
            has_precomputed_context=True,
        )
        self.assertIsNone(result)


class BaselineGuardTests(unittest.TestCase):
    def test_guard_restores_baseline_iambic(self) -> None:
        result = metermeter_cli._try_baseline_guard(
            meter_name="trochaic pentameter", conf=0.70,
            baseline_meter="iambic pentameter",
            baseline_conf=0.80,
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "iambic pentameter")
        self.assertEqual(result[2], "baseline_guard")

    def test_guard_no_override_when_baseline_not_iambic(self) -> None:
        result = metermeter_cli._try_baseline_guard(
            meter_name="trochaic pentameter", conf=0.70,
            baseline_meter="trochaic pentameter",
            baseline_conf=0.80,
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNone(result)

    def test_guard_no_override_when_baseline_conf_low(self) -> None:
        result = metermeter_cli._try_baseline_guard(
            meter_name="trochaic pentameter", conf=0.70,
            baseline_meter="iambic pentameter",
            baseline_conf=0.60,  # Below BASELINE_GUARD_CONF_MIN=0.75
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNone(result)

    def test_guard_no_override_when_llm_very_confident(self) -> None:
        result = metermeter_cli._try_baseline_guard(
            meter_name="trochaic pentameter", conf=0.90,  # Above BASELINE_GUARD_LLM_MAX_CONF=0.85
            baseline_meter="iambic pentameter",
            baseline_conf=0.80,
            stress_pattern="USUSUSUSUS",
        )
        self.assertIsNone(result)

    def test_guard_suppressed_with_precomputed_context(self) -> None:
        result = metermeter_cli._try_baseline_guard(
            meter_name="trochaic pentameter", conf=0.70,
            baseline_meter="iambic pentameter",
            baseline_conf=0.80,
            stress_pattern="USUSUSUSUS",
            has_precomputed_context=True,
        )
        self.assertIsNone(result)


class DominantSmoothingTests(unittest.TestCase):
    def test_smoothing_overrides_low_confidence_non_dominant(self) -> None:
        engine = MeterEngine()
        result = metermeter_cli._try_dominant_smoothing(
            meter_name="trochaic pentameter", conf=0.50,
            stress_pattern="USUSUSUSUS",
            dominant_meter="iambic pentameter",
            dominant_ratio=0.85, dominant_line_count=12,
            engine=engine,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "iambic pentameter")
        self.assertEqual(result[2], "dominant_smoothing")

    def test_smoothing_no_override_when_already_dominant(self) -> None:
        engine = MeterEngine()
        result = metermeter_cli._try_dominant_smoothing(
            meter_name="iambic pentameter", conf=0.50,
            stress_pattern="USUSUSUSUS",
            dominant_meter="iambic pentameter",
            dominant_ratio=0.85, dominant_line_count=12,
            engine=engine,
        )
        self.assertIsNone(result)

    def test_smoothing_no_override_when_ratio_low(self) -> None:
        engine = MeterEngine()
        result = metermeter_cli._try_dominant_smoothing(
            meter_name="trochaic pentameter", conf=0.50,
            stress_pattern="USUSUSUSUS",
            dominant_meter="iambic pentameter",
            dominant_ratio=0.60,  # Below DOMINANT_RATIO_MIN=0.75
            dominant_line_count=12,
            engine=engine,
        )
        self.assertIsNone(result)

    def test_smoothing_no_override_when_too_few_lines(self) -> None:
        engine = MeterEngine()
        result = metermeter_cli._try_dominant_smoothing(
            meter_name="trochaic pentameter", conf=0.50,
            stress_pattern="USUSUSUSUS",
            dominant_meter="iambic pentameter",
            dominant_ratio=0.85,
            dominant_line_count=3,  # Below DOMINANT_MIN_LINES=6
            engine=engine,
        )
        self.assertIsNone(result)

    def test_smoothing_no_override_when_confidence_high(self) -> None:
        engine = MeterEngine()
        result = metermeter_cli._try_dominant_smoothing(
            meter_name="trochaic pentameter", conf=0.80,  # Above DOMINANT_LOW_CONF=0.65
            stress_pattern="USUSUSUSUS",
            dominant_meter="iambic pentameter",
            dominant_ratio=0.85, dominant_line_count=12,
            engine=engine,
        )
        self.assertIsNone(result)

    def test_smoothing_no_override_when_no_dominant(self) -> None:
        engine = MeterEngine()
        result = metermeter_cli._try_dominant_smoothing(
            meter_name="trochaic pentameter", conf=0.50,
            stress_pattern="USUSUSUSUS",
            dominant_meter="",
            dominant_ratio=0.85, dominant_line_count=12,
            engine=engine,
        )
        self.assertIsNone(result)


class WeightedDominantMeterTests(unittest.TestCase):
    def test_empty_returns_empty(self) -> None:
        meter, ratio, n = metermeter_cli._weighted_dominant_meter({})
        self.assertEqual(meter, "")
        self.assertEqual(ratio, 0.0)
        self.assertEqual(n, 0)

    def test_single_meter_dominates(self) -> None:
        class _Mock:
            def __init__(self, m, c):
                self.meter_name = m
                self.confidence = c
        refined = {0: _Mock("iambic pentameter", 0.9), 1: _Mock("iambic pentameter", 0.8)}
        meter, ratio, n = metermeter_cli._weighted_dominant_meter(refined)
        self.assertEqual(meter, "iambic pentameter")
        self.assertAlmostEqual(ratio, 1.0)
        self.assertEqual(n, 2)

    def test_mixed_meters_picks_dominant(self) -> None:
        class _Mock:
            def __init__(self, m, c):
                self.meter_name = m
                self.confidence = c
        refined = {
            0: _Mock("iambic pentameter", 0.9),
            1: _Mock("iambic pentameter", 0.8),
            2: _Mock("trochaic tetrameter", 0.5),
        }
        meter, ratio, n = metermeter_cli._weighted_dominant_meter(refined)
        self.assertEqual(meter, "iambic pentameter")
        self.assertGreater(ratio, 0.5)
        self.assertEqual(n, 3)


class OverridePriorityTests(unittest.TestCase):
    def test_priority_order_is_rescore_first(self) -> None:
        self.assertEqual(metermeter_cli._METER_OVERRIDES[0], metermeter_cli._try_pattern_rescore)
        self.assertEqual(metermeter_cli._METER_OVERRIDES[1], metermeter_cli._try_iambic_guard)
        self.assertEqual(metermeter_cli._METER_OVERRIDES[2], metermeter_cli._try_baseline_guard)
        self.assertEqual(metermeter_cli._METER_OVERRIDES[3], metermeter_cli._try_dominant_smoothing)

    def test_first_match_wins(self) -> None:
        # Pattern rescore should fire and prevent iambic guard from firing.
        engine = MeterEngine()
        ctx = dict(
            meter_name="trochaic tetrameter", conf=0.50,
            pattern_best_meter="iambic pentameter",
            pattern_best_score=0.85, pattern_best_margin=0.15,
            stress_pattern="USUSUSUSUS",
            baseline_meter="iambic pentameter", baseline_conf=0.80,
            dominant_meter="iambic pentameter", dominant_ratio=0.85,
            dominant_line_count=12, engine=engine,
        )
        for override_fn in metermeter_cli._METER_OVERRIDES:
            result = override_fn(**ctx)
            if result is not None:
                self.assertEqual(result[2], "pattern_rescore")
                break
        else:
            self.fail("No override fired")
