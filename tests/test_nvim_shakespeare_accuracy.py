import os
import sys
import unittest


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

from metermeter.meter_engine import MeterEngine  # noqa: E402


SONNET_116 = [
    "Let me not to the marriage of true minds",
    "Admit impediments. Love is not love",
    "Which alters when it alteration finds,",
    "Or bends with the remover to remove:",
    "O no! it is an ever-fixed mark",
    "That looks on tempests and is never shaken;",
    "It is the star to every wandering bark,",
    "Whose worth's unknown, although his height be taken.",
    "Love's not Time's fool, though rosy lips and cheeks",
    "Within his bending sickle's compass come;",
    "Love alters not with his brief hours and weeks,",
    "But bears it out even to the edge of doom.",
    "If this be error and upon me proved,",
    "I never writ, nor no man ever loved.",
]


SONNET_130 = [
    "My mistress' eyes are nothing like the sun;",
    "Coral is far more red than her lips' red;",
    "If snow be white, why then her breasts are dun;",
    "If hairs be wires, black wires grow on her head.",
    "I have seen roses damasked, red and white,",
    "But no such roses see I in her cheeks;",
    "And in some perfumes is there more delight",
    "Than in the breath that from my mistress reeks.",
    "I love to hear her speak, yet well I know",
    "That music hath a far more pleasing sound;",
    "I grant I never saw a goddess go;",
    "My mistress, when she walks, treads on the ground.",
    "And yet, by heaven, I think my love as rare",
    "As any she belied with false compare.",
]


def _accuracy(lines):
    engine = MeterEngine()
    ok = 0
    mismatches = []
    for i, line in enumerate(lines, 1):
        got = engine.analyze_line(line, line_no=i)
        if got is None:
            mismatches.append(f"{i}: no analysis")
            continue
        if got.meter_name == "iambic pentameter":
            ok += 1
        else:
            mismatches.append(f"{i}: {got.meter_name}")
    return ok / float(len(lines)), mismatches


class ShakespeareAccuracyTests(unittest.TestCase):
    def test_sonnet_116_baseline_floor(self) -> None:
        acc, mismatches = _accuracy(SONNET_116)
        self.assertGreaterEqual(
            acc,
            0.70,
            "sonnet116 regression: {:.1%}\n{}".format(acc, "\n".join(mismatches)),
        )

    def test_sonnet_130_baseline_floor(self) -> None:
        acc, mismatches = _accuracy(SONNET_130)
        self.assertGreaterEqual(
            acc,
            0.70,
            "sonnet130 regression: {:.1%}\n{}".format(acc, "\n".join(mismatches)),
        )

