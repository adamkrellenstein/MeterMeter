import os
import sys
import unittest


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

from metermeter.meter_engine import MeterEngine  # noqa: E402


class BuiltinLexiconTests(unittest.TestCase):
    def test_apostrophe_entries_are_resolved(self) -> None:
        engine = MeterEngine()
        cases = {
            "heav'n": "S",
            "e'en": "S",
            "ne'er": "S",
            "see'st": "S",
            "possess'd": "US",
            "ruin'd": "SU",
            "nourish'd": "SU",
        }
        for word, expected in cases.items():
            entries, found = engine._resolve_word_entries(word)
            self.assertTrue(found, f"expected builtin lexicon hit for {word}")
            self.assertEqual(entries[0], expected)
