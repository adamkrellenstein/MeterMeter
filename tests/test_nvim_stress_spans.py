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
        # 'pollen' is 2 syllables; we should highlight only the stressed nucleus (vowel group),
        # not the entire token.
        line = "as pollen crowds \\"
        engine = MeterEngine()
        a = engine.analyze_line(line, line_no=0)
        self.assertIsNotNone(a)
        spans = metermeter_cli._stress_spans_for_line(a.source_text, a.token_patterns)
        self.assertTrue(spans, "expected at least one stress span")

        token_start = line.find("pollen")
        self.assertGreaterEqual(token_start, 0)
        token_end = token_start + len("pollen")

        # Convert token [char] range to byte range for comparison.
        b_start = len(line[:token_start].encode("utf-8"))
        b_end = len(line[:token_end].encode("utf-8"))

        # Ensure no span covers the entire token.
        for s, e in spans:
            self.assertFalse(s <= b_start and e >= b_end, (s, e, b_start, b_end))

    def test_silent_e_single_syllable_is_not_whole_word(self) -> None:
        # Words like "glance" have an extra vowel group ("e") but are 1 syllable; we should
        # still highlight only the nucleus, not the whole word.
        line = "a glance might \\"
        engine = MeterEngine()
        a = engine.analyze_line(line, line_no=0)
        self.assertIsNotNone(a)
        spans = metermeter_cli._stress_spans_for_line(a.source_text, a.token_patterns)
        self.assertTrue(spans, "expected at least one stress span")

        token = "glance"
        token_start = line.find(token)
        self.assertGreaterEqual(token_start, 0)
        token_end = token_start + len(token)
        b_start = len(line[:token_start].encode("utf-8"))
        b_end = len(line[:token_end].encode("utf-8"))
        for s, e in spans:
            self.assertFalse(s <= b_start and e >= b_end, (s, e, b_start, b_end))

    def test_monosyllable_span_runs_from_nucleus_to_word_end(self) -> None:
        line = "might"
        spans = metermeter_cli._stress_spans_for_line(line, ["S"])
        self.assertEqual(len(spans), 1)
        s, e = spans[0]
        # "might": vowel group starts at "i" (index 1), highlight should include "ight".
        self.assertEqual(s, 1)
        self.assertEqual(e, len(line))

    def test_apostrophe_token_span_is_within_word(self) -> None:
        line = "thou wander'st in his shade"
        engine = MeterEngine()
        a = engine.analyze_line(line, line_no=0)
        self.assertIsNotNone(a)
        spans = metermeter_cli._stress_spans_for_line(a.source_text, a.token_patterns)
        self.assertTrue(spans, "expected stress spans for apostrophe token")
        token = "wander'st"
        token_start = line.find(token)
        self.assertGreaterEqual(token_start, 0)
        token_end = token_start + len(token)
        b_start = len(line[:token_start].encode("utf-8"))
        b_end = len(line[:token_end].encode("utf-8"))
        for s, e in spans:
            if s >= b_start and s <= b_end:
                self.assertLessEqual(e, b_end)

    def test_char_to_byte_index_ascii(self) -> None:
        text = "hello world"
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 0), 0)
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 5), 5)
        self.assertEqual(metermeter_cli._char_to_byte_index(text, len(text)), len(text))

    def test_char_to_byte_index_multibyte(self) -> None:
        # em-dash is 3 bytes in UTF-8
        text = "to be\u2014or not"
        dash_char = text.index("\u2014")
        byte_at_dash = metermeter_cli._char_to_byte_index(text, dash_char)
        self.assertEqual(byte_at_dash, len("to be".encode("utf-8")))
        byte_after_dash = metermeter_cli._char_to_byte_index(text, dash_char + 1)
        self.assertEqual(byte_after_dash, byte_at_dash + 3)

    def test_char_to_byte_index_accented(self) -> None:
        # e-acute is 2 bytes in UTF-8
        text = "r\u00e9sum\u00e9 and fate"
        # "r" is 1 byte, "e-acute" is 2 bytes, "sum" is 3 bytes, "e-acute" is 2 bytes
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 0), 0)
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 1), 1)   # after "r"
        self.assertEqual(metermeter_cli._char_to_byte_index(text, 2), 3)   # after "r\u00e9"

    def test_stress_spans_with_emdash_prefix(self) -> None:
        # Verify byte offsets are correct when multi-byte chars precede tokens.
        text = "\u2014 might and grace"
        spans = metermeter_cli._stress_spans_for_line(text, ["S", "U", "S"])
        self.assertTrue(spans, "expected stress spans")
        # All span byte offsets must fall within the encoded line.
        encoded_len = len(text.encode("utf-8"))
        for s, e in spans:
            self.assertGreaterEqual(s, 0)
            self.assertLessEqual(e, encoded_len)
            self.assertGreater(e, s)
        # "might" starts at char 2 (after em-dash + space), byte 4 (3-byte dash + space).
        # Its vowel nucleus "i" is at char 3 / byte 5; span should start there.
        first_span_start = spans[0][0]
        self.assertEqual(first_span_start, len("\u2014 m".encode("utf-8")))

    def test_stress_spans_with_accented_token(self) -> None:
        # Line with accented character inside a token.
        text = "the na\u00efve heart"
        spans = metermeter_cli._stress_spans_for_line(text, ["U", "SU", "S"])
        self.assertTrue(spans, "expected stress spans")
        encoded_len = len(text.encode("utf-8"))
        for s, e in spans:
            self.assertGreaterEqual(s, 0)
            self.assertLessEqual(e, encoded_len)
            self.assertGreater(e, s)
