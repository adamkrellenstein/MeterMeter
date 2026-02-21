import unittest

from meter_engine import MeterEngine


class MeterEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = MeterEngine()

    def test_tokenize_keeps_apostrophes(self) -> None:
        tokens = self.engine.tokenize("O heart, don't break.")
        self.assertEqual(tokens, ["O", "heart", "don't", "break"])

    def test_detects_iambic_pentameter_pattern(self) -> None:
        line = "the light the dark the flame the ash the dawn"
        analysis = self.engine.analyze_line(line, line_no=1)
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.meter_name, "iambic pentameter")
        self.assertGreaterEqual(analysis.confidence, 0.8)

    def test_detects_trochaic_tetrameter_pattern(self) -> None:
        line = "falling petals drifting slowly"
        analysis = self.engine.analyze_line(line, line_no=2)
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.meter_name, "trochaic tetrameter")

    def test_detects_anapestic_trimeter_pattern(self) -> None:
        line = "in the light in the dark in the dawn"
        analysis = self.engine.analyze_line(line, line_no=3)
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis.meter_name, "anapestic trimeter")

    def test_oov_words_reduce_confidence(self) -> None:
        known = self.engine.analyze_line("the light the dark", line_no=4)
        unknown = self.engine.analyze_line("zorblax quentari velmora", line_no=5)
        self.assertIsNotNone(known)
        self.assertIsNotNone(unknown)
        self.assertGreater(known.confidence, unknown.confidence)
        self.assertGreater(len(unknown.oov_tokens), 0)

    def test_blank_line_returns_none(self) -> None:
        self.assertIsNone(self.engine.analyze_line("   ", line_no=6))


if __name__ == "__main__":
    unittest.main()
