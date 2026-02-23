import io
import json
import os
import sys
import unittest
import urllib.request
from typing import Dict, List
from unittest.mock import patch


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

import metermeter_cli  # noqa: E402


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _llm_endpoint_reachable() -> bool:
    endpoint = os.environ.get("METERMETER_LLM_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions")
    from urllib.parse import urlparse
    parsed = urlparse(endpoint)
    base = "{}://{}".format(parsed.scheme, parsed.netloc)
    try:
        req = urllib.request.Request(base, method="GET")
        with urllib.request.urlopen(req, timeout=5):
            return True
    except urllib.error.URLError:
        return False
    except Exception:
        return True


SONNET_29 = [
    "When, in disgrace with fortune and men's eyes,",
    "I all alone beweep my outcast state,",
    "And trouble deaf heaven with my bootless cries,",
    "And look upon myself and curse my fate,",
    "Wishing me like to one more rich in hope,",
    "Featured like him, like him with friends possess'd,",
    "Desiring this man's art and that man's scope,",
    "With what I most enjoy contented least;",
    "Yet in these thoughts myself almost despising,",
    "Haply I think on thee, and then my state,",
    "Like to the lark at break of day arising",
    "From sullen earth, sings hymns at heaven's gate;",
    "For thy sweet love remember'd such wealth brings",
    "That then I scorn to change my state with kings.",
]


SONNET_73 = [
    "That time of year thou mayst in me behold",
    "When yellow leaves, or none, or few, do hang",
    "Upon those boughs which shake against the cold,",
    "Bare ruin'd choirs, where late the sweet birds sang.",
    "In me thou seest the twilight of such day",
    "As after sunset fadeth in the west,",
    "Which by and by black night doth take away,",
    "Death's second self, that seals up all in rest.",
    "In me thou see'st the glowing of such fire",
    "That on the ashes of his youth doth lie,",
    "As the death-bed whereon it must expire,",
    "Consumed with that which it was nourish'd by.",
    "This thou perceiv'st, which makes thy love more strong,",
    "To love that well which thou must leave ere long.",
]


TROCHAIC_TETRAMETER = [
    "Tell me not, in mournful numbers,",
    "Life is but an empty dream!",
    "For the soul is dead that slumbers,",
    "And things are not what they seem.",
]


ANAPESTIC_TETRAMETER = [
    "'Twas the night before Christmas, when all through the house,",
    "Not a creature was stirring, not even a mouse;",
    "The stockings were hung by the chimney with care,",
    "In hopes that St. Nicholas soon would be there;",
]


DACTYLIC_HEXAMETER = [
    "This is the forest primeval, the murmuring pines and the hemlocks,",
    "Bearded with moss, and in garments green, indistinct in the twilight,",
    "Stand like Druids of eld, with voices sad and prophetic,",
    "Stand like harpers hoar, with beards that rest on their bosoms.",
]


IAMBIC_TETRAMETER = [
    "Whose woods these are I think I know.",
    "His house is in the village though;",
    "He will not see me stopping here",
    "To watch his woods fill up with snow.",
    "My little horse must think it queer",
    "To stop without a farmhouse near",
    "Between the woods and frozen lake",
    "The darkest evening of the year.",
]


class NvimLLMIntegrationExtendedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _env_bool("METERMETER_LLM_EXTENDED", default=False):
            raise unittest.SkipTest("set METERMETER_LLM_EXTENDED=1 to run extended LLM integration suite")
        if not _llm_endpoint_reachable():
            raise unittest.SkipTest("LLM endpoint not available")

    def _run_cli(self, lines: List[Dict[str, object]], eval_mode: str = "production") -> Dict[str, object]:
        endpoint = os.environ.get("METERMETER_LLM_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions")
        model = os.environ.get("METERMETER_LLM_MODEL", "qwen2.5:7b-instruct")
        timeout_ms = int(os.environ.get("METERMETER_LLM_TIMEOUT_MS", "30000"))
        temp = float(os.environ.get("METERMETER_LLM_TEMPERATURE", "0.0"))
        max_lines = int(os.environ.get("METERMETER_LLM_MAX_LINES_PER_SCAN", "2"))
        req = {
            "config": {
                "llm": {
                    "enabled": True,
                    "endpoint": endpoint,
                    "model": model,
                    "timeout_ms": timeout_ms,
                    "temperature": temp,
                    "max_lines_per_scan": max(1, max_lines),
                    "eval_mode": eval_mode,
                },
                "lexicon_path": "",
            },
            "lines": lines,
        }
        stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            rc = metermeter_cli.main()
        self.assertEqual(rc, 0)
        out = json.loads(stdout.getvalue() or "{}")
        return out if isinstance(out, dict) else {}

    def _collect_results(self, rows: List[str], eval_mode: str = "production") -> Dict[int, dict]:
        batch = int(os.environ.get("METERMETER_LLM_INTEGRATION_BATCH", "2"))
        batch = max(1, batch)
        by_lnum = {}
        for i in range(0, len(rows), batch):
            lines = [{"lnum": j, "text": rows[j]} for j in range(i, min(len(rows), i + batch))]
            out = self._run_cli(lines, eval_mode=eval_mode)
            if out.get("error"):
                self.fail("llm integration error: {}".format(out.get("error")))
            for r in out.get("results") or []:
                if isinstance(r, dict) and isinstance(r.get("lnum"), int):
                    by_lnum[r["lnum"]] = r
        return by_lnum

    def _assert_meter_floor(self, label: str, rows: List[str], expected: str, floor: float) -> None:
        by_lnum = self._collect_results(rows, eval_mode="production")
        hits = 0
        mismatches = []
        for i, _ in enumerate(rows):
            got = by_lnum.get(i)
            self.assertIsNotNone(got, "missing result for line {}".format(i + 1))
            meter = str((got or {}).get("meter_name", "")).strip().lower()
            if meter == expected:
                hits += 1
            else:
                mismatches.append("{}: {}".format(i + 1, meter))
        accuracy = hits / float(len(rows))
        self.assertGreaterEqual(
            accuracy,
            floor,
            "llm {} accuracy regression: {:.1%}\n{}".format(label, accuracy, "\n".join(mismatches)),
        )

    def test_sonnet29_floor(self) -> None:
        self._assert_meter_floor("sonnet29", SONNET_29, "iambic pentameter", floor=0.85)

    def test_sonnet73_floor(self) -> None:
        self._assert_meter_floor("sonnet73", SONNET_73, "iambic pentameter", floor=0.85)

    def test_trochaic_tetrameter_floor(self) -> None:
        self._assert_meter_floor("trochaic", TROCHAIC_TETRAMETER, "trochaic tetrameter", floor=0.60)

    def test_anapestic_tetrameter_floor(self) -> None:
        self._assert_meter_floor("anapestic", ANAPESTIC_TETRAMETER, "anapestic tetrameter", floor=0.60)

    def test_dactylic_hexameter_floor(self) -> None:
        self._assert_meter_floor("dactylic", DACTYLIC_HEXAMETER, "dactylic hexameter", floor=0.60)

    def test_iambic_tetrameter_floor(self) -> None:
        self._assert_meter_floor("iambic-tetrameter", IAMBIC_TETRAMETER, "iambic tetrameter", floor=0.75)
