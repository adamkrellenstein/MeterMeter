import io
import json
import os
import sys
import unittest
from typing import Dict, List
from unittest.mock import patch


def _nvim_python_path() -> str:
    here = os.path.dirname(__file__)
    return os.path.join(here, "..", "nvim", "metermeter.nvim", "python")


sys.path.insert(0, os.path.abspath(_nvim_python_path()))

import metermeter_cli  # noqa: E402
from tests.test_nvim_sonnet18_accuracy import SONNET_18_GOLD  # noqa: E402
from tests.test_nvim_broad_corpora import MILTON_ON_HIS_BLINDNESS  # noqa: E402


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class NvimLLMIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _env_bool("METERMETER_LLM_INTEGRATION", default=False):
            raise unittest.SkipTest("set METERMETER_LLM_INTEGRATION=1 to run real LLM integration tests")

    def _run_cli(self, lines: List[Dict[str, object]]) -> Dict[str, object]:
        endpoint = os.environ.get("METERMETER_LLM_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions")
        model = os.environ.get("METERMETER_LLM_MODEL", "qwen2.5:7b-instruct")
        timeout_ms = int(os.environ.get("METERMETER_LLM_TIMEOUT_MS", "60000"))
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
                }
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

    def test_sonnet18_llm_accuracy_floor(self) -> None:
        lines = [{"lnum": i, "text": row["text"]} for i, row in enumerate(SONNET_18_GOLD)]
        by_lnum = self._collect_results(lines)

        matches = 0
        mismatches = []
        for i, row in enumerate(SONNET_18_GOLD):
            got = by_lnum.get(i)
            self.assertIsNotNone(got, "missing result for line {}".format(i + 1))
            assert got is not None
            self.assertEqual(got.get("source"), "llm", "non-llm source for line {}".format(i + 1))
            got_meter = str(got.get("meter_name", "")).strip().lower()
            if got_meter == row["expected_meter"]:
                matches += 1
            else:
                mismatches.append("{}: {} != {}".format(i + 1, got_meter, row["expected_meter"]))

        accuracy = matches / float(len(SONNET_18_GOLD))
        self.assertGreaterEqual(
            accuracy,
            0.85,
            "llm sonnet18 accuracy regression: {:.1%}\n{}".format(accuracy, "\n".join(mismatches)),
        )

    def _collect_results(self, lines: List[Dict[str, object]]) -> Dict[int, Dict[str, object]]:
        by_lnum = {}
        batch = int(os.environ.get("METERMETER_LLM_INTEGRATION_BATCH", "2"))
        batch = max(1, batch)
        for i in range(0, len(lines), batch):
            out = self._run_cli(lines[i : i + batch])
            if out.get("error"):
                self.fail("llm integration error: {}".format(out.get("error")))
            for r in out.get("results") or []:
                if isinstance(r, dict) and isinstance(r.get("lnum"), int):
                    by_lnum[r["lnum"]] = r
        return by_lnum

    def test_milton_llm_accuracy_floor(self) -> None:
        lines = [{"lnum": i, "text": row} for i, row in enumerate(MILTON_ON_HIS_BLINDNESS)]
        by_lnum = self._collect_results(lines)
        matches = 0
        mismatches = []
        for i, row in enumerate(MILTON_ON_HIS_BLINDNESS):
            got = by_lnum.get(i)
            self.assertIsNotNone(got, "missing result for line {}".format(i + 1))
            assert got is not None
            self.assertEqual(got.get("source"), "llm", "non-llm source for line {}".format(i + 1))
            got_meter = str(got.get("meter_name", "")).strip().lower()
            if got_meter == "iambic pentameter":
                matches += 1
            else:
                mismatches.append("{}: {}".format(i + 1, got_meter))

        accuracy = matches / float(len(MILTON_ON_HIS_BLINDNESS))
        self.assertGreaterEqual(
            accuracy,
            0.60,
            "llm milton accuracy regression: {:.1%}\n{}".format(accuracy, "\n".join(mismatches)),
        )
