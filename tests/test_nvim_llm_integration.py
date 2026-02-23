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
from tests.test_nvim_sonnet18_accuracy import SONNET_18_GOLD  # noqa: E402
from tests.test_nvim_shakespeare_accuracy import SONNET_116, SONNET_130  # noqa: E402
from tests.test_nvim_broad_corpora import MILTON_ON_HIS_BLINDNESS, WHITMAN_SONG_OF_MYSELF_OPENING  # noqa: E402


def _llm_endpoint_reachable() -> bool:
    endpoint = os.environ.get("METERMETER_LLM_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions")
    base = endpoint.rsplit("/", 1)[0] if "/" in endpoint else endpoint
    try:
        req = urllib.request.Request(base, method="HEAD")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False

KNOWN_REGRESSION_LINES = [
    "Nor shall Death brag thou wander'st in his shade,",
    "Let me not to the marriage of true minds",
    "Which alters when it alteration finds,",
    "If snow be white, why then her breasts are dun;",
    "If hairs be wires, black wires grow on her head.",
    "And in some perfumes is there more delight",
    "Lodged with me useless, though my Soul more bent",
]

SCANSION_IAMBIC_GOLD = [
    ("Shall I compare thee to a summer's day?", "USUSUSUSUS"),
    ("And summer's lease hath all too short a date:", "USUSUSUSUS"),
    ("But thy eternal summer shall not fade,", "USUSUSUSUS"),
    ("So long as men can breathe or eyes can see,", "USUSUSUSUS"),
]


class NvimLLMIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _llm_endpoint_reachable():
            raise unittest.SkipTest("LLM endpoint not available")

    def _run_cli(
        self,
        lines: List[Dict[str, object]],
        eval_mode: str = "production",
        context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        endpoint = os.environ.get("METERMETER_LLM_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions")
        model = os.environ.get("METERMETER_LLM_MODEL", "qwen2.5:7b-instruct")
        timeout_ms = int(os.environ.get("METERMETER_LLM_TIMEOUT_MS", "60000"))
        temp = float(os.environ.get("METERMETER_LLM_TEMPERATURE", "0.0"))
        env_eval_mode = str(os.environ.get("METERMETER_LLM_EVAL_MODE", "") or "").strip().lower()
        if eval_mode == "production" and env_eval_mode in {"production", "strict"}:
            eval_mode = env_eval_mode

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
                }
            },
            "lines": lines,
        }
        if context:
            req["config"]["context"] = context
        stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
        stdout = io.StringIO()
        with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
            rc = metermeter_cli.main()
        self.assertEqual(rc, 0)
        out = json.loads(stdout.getvalue() or "{}")
        return out if isinstance(out, dict) else {}

    def _collect_results(
        self,
        lines: List[Dict[str, object]],
        eval_mode: str = "production",
        context: Dict[str, object] = None,
        fail_on_error: bool = True,
    ) -> Dict[str, object]:
        by_lnum = {}
        eval_total = {
            "mode": eval_mode,
            "line_count": 0,
            "result_count": 0,
            "meter_normalizations": 0,
            "token_repairs": 0,
            "strict": eval_mode == "strict",
            "errors": 0,
        }
        batch = int(os.environ.get("METERMETER_LLM_INTEGRATION_BATCH", "2"))
        batch = max(1, batch)
        progress = _env_bool("METERMETER_LLM_PROGRESS", default=False)
        total_batches = (len(lines) + batch - 1) // batch
        batch_idx = 0
        for i in range(0, len(lines), batch):
            batch_idx += 1
            out = self._run_cli(lines[i : i + batch], eval_mode=eval_mode, context=context)
            if out.get("error"):
                if progress:
                    print(
                        "[llm] batch {}/{} error: {}".format(batch_idx, total_batches, out.get("error")),
                        file=sys.stderr,
                    )
                if fail_on_error:
                    self.fail("llm integration error: {}".format(out.get("error")))
                eval_total["errors"] += 1
                continue
            if progress:
                print(
                    "[llm] batch {}/{} ok results={}".format(
                        batch_idx, total_batches, len(out.get("results") or [])
                    ),
                    file=sys.stderr,
                )
            eval_obj = out.get("eval") or {}
            eval_total["line_count"] += int(eval_obj.get("line_count") or 0)
            eval_total["result_count"] += int(eval_obj.get("result_count") or 0)
            eval_total["meter_normalizations"] += int(eval_obj.get("meter_normalizations") or 0)
            eval_total["token_repairs"] += int(eval_obj.get("token_repairs") or 0)
            for r in out.get("results") or []:
                if isinstance(r, dict) and isinstance(r.get("lnum"), int):
                    by_lnum[r["lnum"]] = r
        return {"results": by_lnum, "eval": eval_total}

    def _assert_iambic_accuracy(
        self,
        label: str,
        rows: List[Dict[str, object]],
        floor: float,
        eval_mode: str = "production",
        context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        collected = self._collect_results(rows, eval_mode=eval_mode, context=context)
        by_lnum = collected["results"]
        matches = 0
        mismatches = []
        for i, row in enumerate(rows):
            got = by_lnum.get(i)
            self.assertIsNotNone(got, "missing result for line {}".format(i + 1))
            assert got is not None
            got_meter = str(got.get("meter_name", "")).strip().lower()
            if got_meter == "iambic pentameter":
                matches += 1
            else:
                mismatches.append("{}: {}".format(i + 1, got_meter))

        accuracy = matches / float(len(rows))
        self.assertGreaterEqual(
            accuracy,
            floor,
            "llm {} accuracy regression: {:.1%}\n{}".format(label, accuracy, "\n".join(mismatches)),
        )
        return collected

    def test_sonnet18_llm_accuracy_floor(self) -> None:
        lines = [{"lnum": i, "text": row["text"]} for i, row in enumerate(SONNET_18_GOLD)]
        self._assert_iambic_accuracy("sonnet18", lines, floor=0.92, eval_mode="production")

    def test_shakespeare_llm_accuracy_floors(self) -> None:
        lines_116 = [{"lnum": i, "text": row} for i, row in enumerate(SONNET_116)]
        lines_130 = [{"lnum": i, "text": row} for i, row in enumerate(SONNET_130)]
        self._assert_iambic_accuracy("sonnet116", lines_116, floor=0.85, eval_mode="production")
        self._assert_iambic_accuracy("sonnet130", lines_130, floor=0.80, eval_mode="production")

    def test_milton_llm_accuracy_floor(self) -> None:
        lines = [{"lnum": i, "text": row} for i, row in enumerate(MILTON_ON_HIS_BLINDNESS)]
        self._assert_iambic_accuracy("milton", lines, floor=0.85, eval_mode="production")

    def test_known_regression_lines_floor(self) -> None:
        lines = [{"lnum": i, "text": row} for i, row in enumerate(KNOWN_REGRESSION_LINES)]
        dominant_ctx = {
            "dominant_meter": "iambic pentameter",
            "dominant_ratio": 0.85,
            "dominant_line_count": 12,
        }
        collected = self._assert_iambic_accuracy(
            "known-lines",
            lines,
            floor=0.55,
            eval_mode="production",
            context=dominant_ctx,
        )
        by_lnum = collected["results"]
        self.assertGreaterEqual(len(by_lnum), len(lines))
        for i in range(len(lines)):
            got = by_lnum.get(i)
            self.assertIsNotNone(got, "missing known-line result for line {}".format(i + 1))

    def test_whitman_llm_not_overcollapsed(self) -> None:
        lines = [{"lnum": i, "text": row} for i, row in enumerate(WHITMAN_SONG_OF_MYSELF_OPENING)]
        collected = self._collect_results(lines, eval_mode="production")
        by_lnum = collected["results"]
        self.assertGreaterEqual(
            len(by_lnum) / float(len(lines)),
            0.95,
            "whitman coverage regression: {} of {} lines".format(len(by_lnum), len(lines)),
        )
        counts: Dict[str, int] = {}
        for i in range(len(lines)):
            got = by_lnum.get(i)
            self.assertIsNotNone(got, "missing whitman result for line {}".format(i + 1))
            meter_name = str(got.get("meter_name", "")).strip().lower()
            counts[meter_name] = counts.get(meter_name, 0) + 1
        dominant = max(counts.values()) if counts else 0
        dominant_ratio = dominant / float(len(lines))
        self.assertLessEqual(
            dominant_ratio,
            0.50,
            "whitman over-collapse regression: dominant_ratio={:.1%} {}".format(dominant_ratio, counts),
        )

    def test_llm_scansion_quality_floor(self) -> None:
        rows = [{"lnum": i, "text": text} for i, (text, _) in enumerate(SCANSION_IAMBIC_GOLD)]
        collected = self._collect_results(rows, eval_mode="production")
        by_lnum = collected["results"]
        scores = []
        mismatches = []
        for i, (_, expected) in enumerate(SCANSION_IAMBIC_GOLD):
            got = by_lnum.get(i)
            self.assertIsNotNone(got, "missing scansion result for line {}".format(i + 1))
            token_patterns = (got or {}).get("token_patterns") or []
            if not token_patterns:
                mismatches.append("{}: empty patterns".format(i + 1))
                scores.append(0.0)
                continue
            actual = "".join(str(p) for p in token_patterns)
            if len(actual) != len(expected):
                mismatches.append("{}: len {} != {}".format(i + 1, len(actual), len(expected)))
                scores.append(0.0)
                continue
            matches = sum(1 for a, b in zip(actual, expected) if a == b)
            scores.append(matches / float(len(expected)))
        avg_score = sum(scores) / float(len(scores)) if scores else 0.0
        self.assertGreaterEqual(
            avg_score,
            0.80,
            "llm scansion quality regression: {:.1%}\n{}".format(avg_score, "\n".join(mismatches)),
        )

    def test_strict_eval_tracks_raw_quality(self) -> None:
        lines = [{"lnum": i, "text": row["text"]} for i, row in enumerate(SONNET_18_GOLD)]
        collected = self._collect_results(lines, eval_mode="strict", fail_on_error=False)
        by_lnum = collected["results"]
        coverage = len(by_lnum) / float(len(lines))
        if coverage <= 0.0:
            eval_obj = collected["eval"]
            self.assertGreaterEqual(eval_obj.get("errors", 0), 1)
            return
        matches = 0
        total = 0
        for i in range(len(lines)):
            got = by_lnum.get(i)
            if got is None:
                continue
            total += 1
            got_meter = str(got.get("meter_name", "")).strip().lower()
            if got_meter == "iambic pentameter":
                matches += 1
        self.assertGreater(total, 0, "strict mode returned no valid lines")
        accuracy = matches / float(total)
        self.assertGreaterEqual(
            accuracy,
            0.60,
            "strict sonnet18 accuracy regression: {:.1%} over {} returned lines".format(accuracy, total),
        )
        eval_obj = collected["eval"]
        self.assertEqual(eval_obj.get("mode"), "strict")
        self.assertEqual(eval_obj.get("meter_normalizations"), 0)
        self.assertEqual(eval_obj.get("token_repairs"), 0)
        self.assertGreaterEqual(eval_obj.get("errors", 0), 0)
