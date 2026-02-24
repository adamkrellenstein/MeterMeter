#!/usr/bin/env python3
import json
import re
import sys
from typing import List, Tuple

from metermeter.meter_engine import LineAnalysis, MeterEngine

WORD_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
VOWEL_GROUP_RE = re.compile(r"[AEIOUYaeiouy]+")


def _even_spans(length: int, target: int) -> List[Tuple[int, int]]:
    if length <= 0:
        return []
    target = max(1, min(int(target), int(length)))
    out: List[Tuple[int, int]] = []
    for idx in range(target):
        start = (idx * length) // target
        end = ((idx + 1) * length) // target
        if end <= start:
            end = min(length, start + 1)
        out.append((start, end))
    return out


def _syllable_spans(token: str, syllable_count: int) -> List[Tuple[int, int]]:
    token_len = len(token)
    if token_len <= 0 or syllable_count <= 0:
        return []
    if syllable_count == 1:
        return [(0, token_len)]

    groups = list(VOWEL_GROUP_RE.finditer(token))
    if len(groups) < 2:
        return _even_spans(token_len, syllable_count)

    spans: List[Tuple[int, int]] = []
    current_start = 0
    for idx in range(len(groups) - 1):
        left_end = groups[idx].end()
        right_start = groups[idx + 1].start()
        split = left_end if right_start <= left_end else (left_end + right_start) // 2
        if split <= current_start:
            split = min(token_len, current_start + 1)
        spans.append((current_start, split))
        current_start = split
    spans.append((current_start, token_len))

    if len(spans) != syllable_count:
        return _even_spans(token_len, syllable_count)
    return spans


def _char_to_byte_index(text: str, char_idx: int) -> int:
    if char_idx <= 0:
        return 0
    if char_idx >= len(text):
        return len(text.encode("utf-8"))
    return len(text[:char_idx].encode("utf-8"))


def _stress_spans_from_syllables(text: str, syllable_positions: List[Tuple[str, bool]]) -> List[List[int]]:
    """Compute byte-level stress spans by matching prosodic syllable texts against the line."""
    spans: List[List[int]] = []
    cursor = 0
    text_lower = text.lower()
    for syl_txt, is_strong in syllable_positions:
        syl_lower = syl_txt.lower()
        if not syl_lower:
            continue
        idx = text_lower.find(syl_lower, cursor)
        if idx == -1:
            continue
        end = idx + len(syl_lower)
        cursor = end
        if is_strong:
            b_s = _char_to_byte_index(text, idx)
            b_e = _char_to_byte_index(text, end)
            if b_e > b_s:
                spans.append([b_s, b_e])
    return spans


def _stress_spans_for_line(text: str, token_patterns: List[str]) -> List[List[int]]:
    """Compute byte-level stress spans from per-word token patterns."""
    spans: List[List[int]] = []
    matches = list(WORD_TOKEN_RE.finditer(text))
    limit = min(len(matches), len(token_patterns))
    for i in range(limit):
        pat = token_patterns[i] or ""
        if not pat:
            continue
        m = matches[i]
        token = m.group(0)

        groups = list(VOWEL_GROUP_RE.finditer(token))
        pat_len = len(pat)
        if pat_len >= 1 and len(groups) == pat_len + 1 and token.lower().endswith("e"):
            last = groups[-1].group(0).lower()
            if last == "e":
                groups = groups[:-1]

        if pat_len == 1 and groups:
            syl_spans = [(groups[0].start(), len(token))]
        elif len(groups) == pat_len and groups:
            syl_spans = [(g.start(), g.end()) for g in groups]
        else:
            syl_spans = _syllable_spans(token, pat_len)

        for syl_idx, flag in enumerate(pat):
            if flag != "S":
                continue
            if syl_idx >= len(syl_spans):
                continue
            rel_s, rel_e = syl_spans[syl_idx]
            abs_s = m.start() + rel_s
            abs_e = m.start() + rel_e
            if abs_e <= abs_s:
                continue
            b_s = _char_to_byte_index(text, abs_s)
            b_e = _char_to_byte_index(text, abs_e)
            spans.append([b_s, b_e])
    return spans


def _read_stdin_json() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def main() -> int:
    req = _read_stdin_json()
    lines = req.get("lines") or []

    engine = MeterEngine()
    analyses: List[LineAnalysis] = []
    for item in lines:
        if not isinstance(item, dict):
            continue
        lnum = item.get("lnum")
        text = item.get("text")
        if not isinstance(lnum, int) or not isinstance(text, str):
            continue
        a = engine.analyze_line(text, line_no=lnum)
        if a is None:
            continue
        analyses.append(a)

    results: List[dict] = []
    for a in analyses:
        spans = _stress_spans_from_syllables(a.source_text, a.syllable_positions)
        results.append({
            "lnum": int(a.line_no),
            "text": a.source_text,
            "meter_name": a.meter_name,
            "confidence": float(a.confidence),
            "stress_spans": spans,
        })

    payload = {
        "results": results,
        "eval": {
            "line_count": len(analyses),
            "result_count": len(results),
        },
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
