#!/usr/bin/env python3
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

from metermeter.meter_engine import MeterEngine


def _char_to_byte_index(text: str, char_idx: int) -> int:
    if char_idx <= 0:
        return 0
    if char_idx >= len(text):
        return len(text.encode("utf-8"))
    return len(text[:char_idx].encode("utf-8"))


def _stress_spans_from_syllables(
    text: str,
    syllable_positions: List[Tuple[str, bool]],
    syllable_char_spans: Optional[List[Tuple[int, int]]] = None,
) -> List[List[int]]:
    """Compute byte-level stress spans from char spans, with substring fallback."""
    spans: List[List[int]] = []
    text_len = len(text)
    if (
        isinstance(syllable_char_spans, list)
        and len(syllable_char_spans) == len(syllable_positions)
    ):
        for (span_start, span_end), (_, is_strong) in zip(syllable_char_spans, syllable_positions):
            if not is_strong:
                continue
            s = max(0, min(text_len, int(span_start)))
            e = max(0, min(text_len, int(span_end)))
            if e <= s:
                continue
            b_s = _char_to_byte_index(text, s)
            b_e = _char_to_byte_index(text, e)
            if b_e > b_s:
                spans.append([b_s, b_e])
        return spans

    # Backward-compatible fallback.
    cursor = 0
    text_lower = text.lower()
    for syl_txt, is_strong in syllable_positions:
        syl_lower = syl_txt.lower()
        if not syl_lower:
            continue
        idx = text_lower.find(syl_lower, cursor)
        if idx == -1:
            continue
        syl_end = idx + len(syl_lower)
        cursor = syl_end
        if is_strong:
            b_s = _char_to_byte_index(text, idx)
            b_e = _char_to_byte_index(text, syl_end)
            if b_e > b_s:
                spans.append([b_s, b_e])
    return spans


def _analyze_line(engine: MeterEngine, item: dict, context: Optional[Dict[str, Any]] = None) -> dict | None:
    lnum = item.get("lnum")
    text = item.get("text")
    if not isinstance(lnum, int) or not isinstance(text, str):
        return None
    a = engine.analyze_line(text, line_no=lnum, context=context)
    if a is None:
        return None
    spans = _stress_spans_from_syllables(
        a.source_text,
        a.syllable_positions,
        getattr(a, "syllable_char_spans", None),
    )
    return {
        "lnum": int(a.line_no),
        "text": a.source_text,
        "meter_name": a.meter_name,
        "confidence": float(a.confidence),
        "meter_features": engine.meter_features_for(a.meter_name, a.stress_pattern),
        "stress_spans": spans,
    }


def run_persistent() -> int:
    """Persistent mode: read newline-delimited JSON requests, respond on stdout."""
    engine = MeterEngine()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if req.get("shutdown"):
            break

        req_id = req.get("id")
        context = req.get("context")
        if not isinstance(context, dict):
            context = None
        items = [
            item for item in (req.get("lines") or [])
            if isinstance(item, dict)
            and isinstance(item.get("lnum"), int)
            and isinstance(item.get("text"), str)
        ]

        results = [r for item in items for r in [_analyze_line(engine, item, context=context)] if r is not None]

        payload = {
            "id": req_id,
            "results": results,
            "eval": {"line_count": len(items), "result_count": len(results)},
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
        sys.stdout.flush()

    return 0


def main() -> int:
    """One-shot mode: read a single JSON request from stdin, write response, exit."""
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    req = json.loads(raw)
    context = req.get("context")
    if not isinstance(context, dict):
        context = None
    items = [
        item for item in (req.get("lines") or [])
        if isinstance(item, dict)
        and isinstance(item.get("lnum"), int)
        and isinstance(item.get("text"), str)
    ]

    engine = MeterEngine()
    results = [r for item in items for r in [_analyze_line(engine, item, context=context)] if r is not None]

    payload = {
        "results": results,
        "eval": {"line_count": len(items), "result_count": len(results)},
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "persistent"
    if mode == "oneshot":
        raise SystemExit(main())
    else:
        raise SystemExit(run_persistent())
