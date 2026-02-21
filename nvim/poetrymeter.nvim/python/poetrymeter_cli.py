#!/usr/bin/env python3
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from poetrymeter.meter_engine import LineAnalysis, MeterEngine
from poetrymeter.llm_refiner import LLMRefiner

WORD_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
VOWEL_GROUP_RE = re.compile(r"[AEIOUYaeiouy]+")


def _even_spans(length: int, target: int) -> List[Tuple[int, int]]:
    if length <= 0:
        return []
    target = max(1, min(int(target), int(length)))
    out: List[Tuple[int, int]] = []
    for idx in range(target):
        start = int((idx * length) / float(target))
        end = int(((idx + 1) * length) / float(target))
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


def _stress_spans_for_line(text: str, token_patterns: List[str]) -> List[List[int]]:
    spans: List[List[int]] = []
    matches = list(WORD_TOKEN_RE.finditer(text))
    limit = min(len(matches), len(token_patterns))
    for i in range(limit):
        pat = token_patterns[i] or ""
        if not pat:
            continue
        m = matches[i]
        token = m.group(0)

        # Prefer highlighting the vowel nuclei for each syllable (more visible than trying to
        # guess full syllable boundaries). If vowel-group count mismatches, fall back.
        groups = list(VOWEL_GROUP_RE.finditer(token))
        pat_len = len(pat)
        # Common mismatch: silent trailing "e" creates an extra vowel group (e.g. "glance", "bare").
        if pat_len >= 1 and len(groups) == pat_len + 1 and token.lower().endswith("e"):
            last = groups[-1].group(0).lower()
            if last == "e":
                groups = groups[:-1]

        if pat_len == 1 and groups:
            # For 1-syllable tokens, highlight from the vowel nucleus to the end of the token.
            # This is usually what readers perceive as the stressed "chunk" (e.g. mIGHT, glANCE).
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
    config = req.get("config") or {}
    llm_cfg = (config.get("llm") or {}) if isinstance(config, dict) else {}

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

    refined: Dict[int, object] = {}
    llm_enabled = bool(llm_cfg.get("enabled", False))
    endpoint = str(llm_cfg.get("endpoint", "") or "").strip()
    model = str(llm_cfg.get("model", "") or "").strip()
    timeout_ms = int(llm_cfg.get("timeout_ms", 30000))
    temp = float(llm_cfg.get("temperature", 0.1))
    max_llm = int(llm_cfg.get("max_lines_per_scan", 0))

    if llm_enabled and endpoint and model and max_llm > 0 and analyses:
        try:
            refiner = LLMRefiner(endpoint=endpoint, model=model, api_key=str(llm_cfg.get("api_key", "") or ""))
            subset = analyses[: max_llm]
            refined = refiner.refine_lines(subset, timeout_ms=timeout_ms, temperature=temp)
        except Exception:
            refined = {}

    results: List[dict] = []
    for a in analyses:
        r = refined.get(a.line_no)
        meter_name = a.meter_name
        conf = a.confidence
        hint = ""
        token_patterns = a.token_patterns
        source = "engine"
        if r is not None:
            meter_name = getattr(r, "meter_name", meter_name) or meter_name
            conf = getattr(r, "confidence", conf) or conf
            hint = getattr(r, "analysis_hint", "") or ""
            token_patterns = getattr(r, "token_patterns", token_patterns) or token_patterns
            source = "llm"

        spans = _stress_spans_for_line(a.source_text, token_patterns)
        results.append(
            {
                "lnum": int(a.line_no),
                "text": a.source_text,
                "meter_name": meter_name,
                "confidence": float(conf),
                "source": source,
                "hint": hint,
                "token_patterns": token_patterns,
                "stress_spans": spans,
            }
        )

    sys.stdout.write(json.dumps({"results": results}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
