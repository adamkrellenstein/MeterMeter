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
