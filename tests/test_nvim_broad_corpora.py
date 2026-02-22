import os
import sys
import unittest
from collections import Counter


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

from metermeter.meter_engine import MeterEngine  # noqa: E402


MILTON_ON_HIS_BLINDNESS = [
    "When I consider how my light is spent,",
    "Ere half my days in this dark world and wide,",
    "And that one Talent which is death to hide,",
    "Lodged with me useless, though my Soul more bent",
    "To serve therewith my Maker, and present",
    "My true account, lest he returning chide,",
    "Doth God exact day-labour, light denied,",
    "I fondly ask; But patience to prevent",
    "That murmur, soon replies, God doth not need",
    "Either man's work or his own gifts, who best",
    "Bear his mild yoke, they serve him best, his State",
    "Is Kingly. Thousands at his bidding speed",
    "And post o'er Land and Ocean without rest:",
    "They also serve who only stand and wait.",
]


WHITMAN_SONG_OF_MYSELF_OPENING = [
    "I celebrate myself, and sing myself,",
    "And what I assume you shall assume,",
    "For every atom belonging to me as good belongs to you.",
    "I loafe and invite my soul,",
    "I lean and loafe at my ease observing a spear of summer grass.",
    "My tongue, every atom of my blood, form'd from this soil, this air,",
    "Born here of parents born here from parents the same, and their parents the same,",
    "I, now thirty-seven years old in perfect health begin,",
    "Hoping to cease not till death.",
    "Creeds and schools in abeyance,",
    "Retiring back a while sufficed at what they are, but never forgotten,",
    "I harbor for good or bad, I permit to speak at every hazard,",
    "Nature without check with original energy.",
]


class BroadCorpusAccuracyTests(unittest.TestCase):
    def test_milton_iambic_pentameter_floor(self) -> None:
        # Non-Shakespeare formal benchmark (public domain) to reduce overfitting.
        engine = MeterEngine()
        hits = 0
        mismatches = []
        for i, line in enumerate(MILTON_ON_HIS_BLINDNESS, 1):
            got = engine.analyze_line(line, line_no=i)
            self.assertIsNotNone(got, f"milton line {i} returned no analysis")
            assert got is not None
            if got.meter_name == "iambic pentameter":
                hits += 1
            else:
                mismatches.append(f"{i}: {got.meter_name}")
        acc = hits / float(len(MILTON_ON_HIS_BLINDNESS))
        self.assertGreaterEqual(
            acc,
            0.75,
            "milton regression: {:.1%}\n{}".format(acc, "\n".join(mismatches)),
        )

    def test_whitman_free_verse_not_forced_to_single_meter(self) -> None:
        # Free-verse guardrail: model should not collapse most lines into one meter class.
        engine = MeterEngine()
        meters = Counter()
        for i, line in enumerate(WHITMAN_SONG_OF_MYSELF_OPENING, 1):
            got = engine.analyze_line(line, line_no=i)
            self.assertIsNotNone(got, f"whitman line {i} returned no analysis")
            assert got is not None
            meters[got.meter_name] += 1

        dominant = meters.most_common(1)[0][1]
        dominant_ratio = dominant / float(len(WHITMAN_SONG_OF_MYSELF_OPENING))
        self.assertLessEqual(
            dominant_ratio,
            0.60,
            "whitman regression: dominant meter ratio too high ({:.1%}) {}".format(
                dominant_ratio, dict(meters)
            ),
        )

