import json
import os
import sys
import unittest


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

import metermeter_cli  # noqa: E402
from metermeter.meter_engine import MeterEngine  # noqa: E402


class NvimStressSpanTests(unittest.TestCase):
    def test_multisyllable_token_span_is_not_whole_word(self) -> None:
        # 'pollen' is 2 syllables; we should highlight only the stressed nucleus,
        # not the entire token.
        line = "as pollen crowds \\"
        engine = MeterEngine()
        a = engine.analyze_line(line, line_no=0)
        self.assertIsNotNone(a)
        spans = metermeter_cli._stress_spans_from_syllables(a.source_text, a.syllable_positions)
        self.assertTrue(spans, "expected at least one stress span")

        token_start = line.find("pollen")
        self.assertGreaterEqual(token_start, 0)
        token_end = token_start + len("pollen")

        b_start = len(line[:token_start].encode("utf-8"))
        b_end = len(line[:token_end].encode("utf-8"))

        for s, e in spans:
            self.assertFalse(s <= b_start and e >= b_end, (s, e, b_start, b_end))

    def test_silent_e_single_syllable_spans_within_line(self) -> None:
        # Prosodic correctly identifies "glance" as 1 syllable; the span should
        # be within the line bounds.
        line = "a glance might \\"
        engine = MeterEngine()
        a = engine.analyze_line(line, line_no=0)
        self.assertIsNotNone(a)
        spans = metermeter_cli._stress_spans_from_syllables(a.source_text, a.syllable_positions)
        self.assertTrue(spans, "expected at least one stress span")
        encoded_len = len(line.encode("utf-8"))
        for s, e in spans:
            self.assertGreaterEqual(s, 0)
            self.assertLessEqual(e, encoded_len)
            self.assertGreater(e, s)

    def test_monosyllable_span_covers_nucleus(self) -> None:
        # _stress_spans_from_syllables matches the syllable text directly.
        # For "might" the syllable text is the whole word, so the span covers it.
        line = "might"
        spans = metermeter_cli._stress_spans_from_syllables(line, [("might", True)])
        self.assertEqual(len(spans), 1)
        s, e = spans[0]
        self.assertEqual(s, 0)
        self.assertEqual(e, len(line.encode("utf-8")))

    def test_apostrophe_token_span_is_within_line(self) -> None:
        line = "thou wander'st in his shade"
        engine = MeterEngine()
        a = engine.analyze_line(line, line_no=0)
        self.assertIsNotNone(a)
        spans = metermeter_cli._stress_spans_from_syllables(a.source_text, a.syllable_positions)
        self.assertTrue(spans, "expected stress spans for apostrophe token")
        encoded_len = len(line.encode("utf-8"))
        for s, e in spans:
            self.assertGreaterEqual(s, 0)
            self.assertLessEqual(e, encoded_len)
            self.assertGreater(e, s)

    def test_char_to_byte_index_ascii(self) -> None:
        text = "hello world"
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 0), 0)
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 5), 5)
        self.assertEqual(metermeter_cli._char_to_byte_index(text, len(text)), len(text))

    def test_char_to_byte_index_multibyte(self) -> None:
        text = "to be\u2014or not"
        dash_char = text.index("\u2014")
        byte_at_dash = metermeter_cli._char_to_byte_index(text, dash_char)
        self.assertEqual(byte_at_dash, len("to be".encode("utf-8")))
        byte_after_dash = metermeter_cli._char_to_byte_index(text, dash_char + 1)
        self.assertEqual(byte_after_dash, byte_at_dash + 3)

    def test_char_to_byte_index_accented(self) -> None:
        text = "r\u00e9sum\u00e9 and fate"
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 0), 0)
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 1), 1)
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 2), 3)

    def test_stress_spans_from_syllables_emdash_prefix(self) -> None:
        # Byte offsets are correct when multi-byte chars precede syllables.
        text = "\u2014 might and grace"
        # "might" starts at char 2 (after em-dash + space).
        spans = metermeter_cli._stress_spans_from_syllables(text, [("might", True), ("and", False), ("grace", True)])
        self.assertTrue(spans, "expected stress spans")
        encoded_len = len(text.encode("utf-8"))
        for s, e in spans:
            self.assertGreaterEqual(s, 0)
            self.assertLessEqual(e, encoded_len)
            self.assertGreater(e, s)
        # First span should be "might" starting after the em-dash and space.
        first_start = spans[0][0]
        self.assertEqual(first_start, len("\u2014 ".encode("utf-8")))

    def test_stress_spans_from_syllables_accented_token(self) -> None:
        text = "the na\u00efve heart"
        spans = metermeter_cli._stress_spans_from_syllables(text, [("the", False), ("na", True), ("heart", True)])
        self.assertTrue(spans, "expected stress spans")
        encoded_len = len(text.encode("utf-8"))
        for s, e in spans:
            self.assertGreaterEqual(s, 0)
            self.assertLessEqual(e, encoded_len)
            self.assertGreater(e, s)

    def test_stress_spans_for_line_fallback_monosyllable(self) -> None:
        # _stress_spans_for_line is kept for the LLM override path.
        line = "might"
        spans = metermeter_cli._stress_spans_for_line(line, ["S"])
        self.assertEqual(len(spans), 1)
        s, e = spans[0]
        self.assertEqual(s, 1)  # vowel nucleus "i" at index 1
        self.assertEqual(e, len(line))
