import os
import sys
import unittest


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

from metermeter.heuristics import estimate_syllables, estimate_stress_pattern  # noqa: E402


class HeuristicSyllableTests(unittest.TestCase):
    def test_elision_overrides(self) -> None:
        self.assertEqual(estimate_syllables("heaven"), 1)
        self.assertEqual(estimate_syllables("Heaven"), 1)
        self.assertEqual(estimate_syllables("every"), 2)
        self.assertEqual(estimate_syllables("o'er"), 1)
        self.assertEqual(estimate_syllables("power"), 1)
        self.assertEqual(estimate_syllables("hour"), 1)

    def test_basic_syllable_floor(self) -> None:
        for word in ["wind", "summer", "temperate", "darling", "mistress"]:
            self.assertGreaterEqual(estimate_syllables(word), 1)

    def test_function_word_stress(self) -> None:
        self.assertEqual(estimate_stress_pattern("the"), "U")
        self.assertEqual(estimate_stress_pattern("and"), "U")
        self.assertEqual(estimate_stress_pattern("to"), "U")
