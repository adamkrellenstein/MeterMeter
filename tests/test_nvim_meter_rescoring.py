import unittest

from metermeter.meter_engine import MeterEngine


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

    def test_9_syllable_iambic_not_pentameter(self) -> None:
        engine = MeterEngine()
        meter, score, debug = engine.best_meter_for_stress_pattern("USUSUSUSU")
        self.assertEqual(meter, "iambic tetrameter")

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

    def test_function_word_priors_are_soft(self) -> None:
        engine = MeterEngine()
        options = dict(engine._options_for_syllable("to", True, lexical_stressed=False))
        self.assertIn("U", options)
        self.assertIn("S", options)
        self.assertEqual(options["U"], 0.0)
        self.assertGreater(options["S"], 0.0)

    def test_context_prior_can_raise_confidence(self) -> None:
        engine = MeterEngine()
        line = "Shall I compare thee to a summer's day?"
        baseline = engine.analyze_line(line, line_no=0)
        self.assertIsNotNone(baseline)
        assert baseline is not None

        boosted = engine.analyze_line(
            line,
            line_no=0,
            context={
                "dominant_meter": baseline.meter_name,
                "dominant_strength": 1.0,
            },
        )
        self.assertIsNotNone(boosted)
        assert boosted is not None
        self.assertGreaterEqual(boosted.confidence, baseline.confidence)
        self.assertGreaterEqual(float(boosted.debug_scores.get("context_bonus") or 0.0), 0.0)

    def test_viterbi_pattern_length_matches_syllables(self) -> None:
        engine = MeterEngine()
        line = "And summer's lease hath all too short a date:"
        analysis = engine.analyze_line(line, line_no=0)
        self.assertIsNotNone(analysis)
        assert analysis is not None
        self.assertEqual(len(analysis.stress_pattern), len(analysis.syllable_positions))
