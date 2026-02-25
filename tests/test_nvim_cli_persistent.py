"""Integration tests for the persistent subprocess protocol in metermeter_cli."""
import io
import json
import sys
import unittest

import metermeter_cli


def _run_persistent(requests: list[dict]) -> list[dict]:
    """Feed newline-delimited JSON requests through run_persistent() and collect responses."""
    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    sys.stdin = io.StringIO(lines)
    captured = io.StringIO()
    sys.stdout = captured
    try:
        metermeter_cli.run_persistent()
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout
    responses = []
    for line in captured.getvalue().splitlines():
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses


class PersistentProtocolTests(unittest.TestCase):
    LINES = [
        {"lnum": 0, "text": "Shall I compare thee to a summers day"},
        {"lnum": 1, "text": "Thou art more lovely and more temperate"},
        {"lnum": 2, "text": "Rough winds do shake the darling buds of May"},
        {"lnum": 3, "text": "And summers lease hath all too short a date"},
    ]

    def test_single_request_returns_results(self) -> None:
        """run_persistent() responds to a single request with results for each line."""
        resps = _run_persistent([{"id": 1, "lines": self.LINES}])
        self.assertEqual(len(resps), 1)
        resp = resps[0]
        self.assertEqual(resp["id"], 1)
        self.assertIsInstance(resp["results"], list)
        self.assertEqual(len(resp["results"]), len(self.LINES))

    def test_response_has_required_fields(self) -> None:
        """Each result has lnum, text, meter_name, confidence, stress_spans."""
        resps = _run_persistent([{"id": 7, "lines": self.LINES[:1]}])
        result = resps[0]["results"][0]
        self.assertIn("lnum", result)
        self.assertIn("text", result)
        self.assertIn("meter_name", result)
        self.assertIn("confidence", result)
        self.assertIn("stress_spans", result)

    def test_multiple_requests_in_sequence(self) -> None:
        """run_persistent() handles multiple requests and echoes the correct id each time."""
        resps = _run_persistent([
            {"id": 10, "lines": self.LINES[:2]},
            {"id": 20, "lines": self.LINES[2:]},
        ])
        self.assertEqual(len(resps), 2)
        self.assertEqual(resps[0]["id"], 10)
        self.assertEqual(resps[1]["id"], 20)
        self.assertEqual(len(resps[0]["results"]), 2)
        self.assertEqual(len(resps[1]["results"]), 2)

    def test_large_batch_returns_all_results(self) -> None:
        """A batch larger than the old pool threshold (>2) returns all results."""
        resps = _run_persistent([{"id": 99, "lines": self.LINES}])
        self.assertEqual(len(resps[0]["results"]), len(self.LINES))

    def test_shutdown_message_exits_cleanly(self) -> None:
        """A shutdown message stops the loop without error."""
        resps = _run_persistent([
            {"id": 1, "lines": self.LINES[:1]},
            {"shutdown": True},
        ])
        self.assertEqual(len(resps), 1)

    def test_empty_lines_field(self) -> None:
        resps = _run_persistent([{"id": 5, "lines": []}])
        self.assertEqual(resps[0]["results"], [])
        self.assertEqual(resps[0]["eval"]["line_count"], 0)

    def test_optional_context_is_accepted(self) -> None:
        resps = _run_persistent([{
            "id": 31,
            "lines": self.LINES[:2],
            "context": {
                "dominant_meter": "iambic pentameter",
                "dominant_strength": 0.75,
            },
        }])
        self.assertEqual(len(resps), 1)
        self.assertEqual(resps[0]["id"], 31)
        self.assertEqual(len(resps[0]["results"]), 2)
