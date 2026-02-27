"""Edge-case unit tests for MeterEngine: empty input, single words, punctuation, etc."""
import unittest

from metermeter.meter_engine import MeterEngine


class EngineEdgeCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = MeterEngine()

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(self.engine.analyze_line(""))

    def test_whitespace_only_returns_none(self) -> None:
        self.assertIsNone(self.engine.analyze_line("   \t  "))

    def test_punctuation_only_returns_none(self) -> None:
        self.assertIsNone(self.engine.analyze_line("...!?---"))

    def test_single_word(self) -> None:
        result = self.engine.analyze_line("hello")
        # Single word should either return a result or None, but never crash.
        if result is not None:
            self.assertEqual(len(result.tokens), 1)
            self.assertGreater(len(result.stress_pattern), 0)

    def test_single_monosyllable(self) -> None:
        result = self.engine.analyze_line("the")
        if result is not None:
            self.assertEqual(len(result.stress_pattern), 1)

    def test_very_long_line(self) -> None:
        # A line with many words should not crash or hang.
        line = " ".join(["the quick brown fox jumps over the lazy dog"] * 5)
        result = self.engine.analyze_line(line)
        if result is not None:
            self.assertGreater(len(result.stress_pattern), 0)

    def test_repeated_word_tokens(self) -> None:
        # Regression test: repeated words should not all map to the first occurrence.
        result = self.engine.analyze_line("love love love")
        if result is not None:
            self.assertEqual(len(result.tokens), 3)
            # Each token should have its own span, not all pointing to index 0.
            spans = result.syllable_char_spans
            if len(spans) >= 3:
                self.assertGreater(spans[1][0], spans[0][0])
                self.assertGreater(spans[2][0], spans[1][0])

    def test_line_with_numbers_ignored(self) -> None:
        # Digits are not matched by TOKEN_RE; engine should handle gracefully.
        result = self.engine.analyze_line("123 456")
        self.assertIsNone(result)

    def test_line_with_mixed_numbers_and_words(self) -> None:
        result = self.engine.analyze_line("the 3rd of May")
        # Should not crash; may or may not return a result depending on prosodic.
        if result is not None:
            self.assertGreater(len(result.stress_pattern), 0)

    def test_apostrophe_contractions(self) -> None:
        result = self.engine.analyze_line("I can't believe it's done")
        if result is not None:
            self.assertGreater(len(result.tokens), 0)
            self.assertGreater(len(result.stress_pattern), 0)

    def test_context_none_is_safe(self) -> None:
        result = self.engine.analyze_line("Shall I compare thee to a summer's day?", context=None)
        self.assertIsNotNone(result)

    def test_context_empty_dict_is_safe(self) -> None:
        result = self.engine.analyze_line("Shall I compare thee to a summer's day?", context={})
        self.assertIsNotNone(result)

    def test_context_invalid_meter_is_safe(self) -> None:
        result = self.engine.analyze_line(
            "Shall I compare thee to a summer's day?",
            context={"dominant_meter": "not a real meter", "dominant_strength": 1.0},
        )
        self.assertIsNotNone(result)

    def test_score_empty_pattern_returns_none(self) -> None:
        self.assertIsNone(self.engine.score_stress_pattern_for_meter("", "iambic pentameter"))

    def test_best_meter_empty_pattern(self) -> None:
        meter, score, debug = self.engine.best_meter_for_stress_pattern("")
        self.assertEqual(meter, "")
        self.assertEqual(score, 0.0)

    def test_tokenize_empty(self) -> None:
        self.assertEqual(self.engine.tokenize(""), [])

    def test_tokenize_punctuation_only(self) -> None:
        self.assertEqual(self.engine.tokenize("...---!!!"), [])

    def test_tokenize_unicode_words(self) -> None:
        self.assertEqual(self.engine.tokenize("café naïve façade"), ["café", "naïve", "façade"])

    def test_tokenize_curly_apostrophes(self) -> None:
        self.assertEqual(self.engine.tokenize("I can’t believe it’s done"), ["I", "can’t", "believe", "it’s", "done"])

    def test_tokenize_leading_curly_apostrophe(self) -> None:
        self.assertEqual(self.engine.tokenize("’tis the season"), ["tis", "the", "season"])

    def test_stress_pattern_length_matches_syllable_positions(self) -> None:
        lines = [
            "Shall I compare thee to a summer's day?",
            "To be or not to be that is the question",
            "Once upon a midnight dreary",
        ]
        for line in lines:
            result = self.engine.analyze_line(line)
            if result is not None:
                self.assertEqual(
                    len(result.stress_pattern),
                    len(result.syllable_positions),
                    f"Pattern/positions length mismatch for: {line}",
                )
