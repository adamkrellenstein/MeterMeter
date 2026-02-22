#!/usr/bin/env python3
import json
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

from metermeter.meter_engine import LineAnalysis, MeterEngine
from metermeter.llm_refiner import LLMRefiner

WORD_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
VOWEL_GROUP_RE = re.compile(r"[AEIOUYaeiouy]+")

RESCORE_MIN_SCORE = 0.72
RESCORE_MIN_MARGIN = 0.10
DOMINANT_RATIO_MIN = 0.75
DOMINANT_MIN_LINES = 6
DOMINANT_LOW_CONF = 0.65
DOMINANT_SCORE_DELTA = 0.08


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


def _weighted_dominant_meter(refined: Dict[int, object]) -> Tuple[str, float, int]:
    counts: Dict[str, float] = {}
    total = 0.0
    n = 0
    for _, r in refined.items():
        meter = str(getattr(r, "meter_name", "") or "").strip().lower()
        if not meter:
            continue
        conf = getattr(r, "confidence", 0.5)
        if not isinstance(conf, (int, float)):
            conf = 0.5
        weight = max(0.05, min(1.0, float(conf)))
        counts[meter] = counts.get(meter, 0.0) + weight
        total += weight
        n += 1
    if not counts or total <= 0.0:
        return "", 0.0, 0
    meter = max(counts.items(), key=lambda kv: kv[1])[0]
    ratio = counts[meter] / total
    return meter, ratio, n


def main() -> int:
    req = _read_stdin_json()
    lines = req.get("lines") or []
    config = req.get("config") or {}
    llm_cfg = (config.get("llm") or {}) if isinstance(config, dict) else {}
    context_cfg = (config.get("context") or {}) if isinstance(config, dict) else {}

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
    error_msg: Optional[str] = None
    llm_enabled = bool(llm_cfg.get("enabled", False))
    endpoint = str(llm_cfg.get("endpoint", "") or "").strip()
    model = str(llm_cfg.get("model", "") or "").strip()
    timeout_ms = int(llm_cfg.get("timeout_ms", 30000))
    temp = float(llm_cfg.get("temperature", 0.1))
    max_llm = int(llm_cfg.get("max_lines_per_scan", 0))
    eval_mode = str(llm_cfg.get("eval_mode", "production") or "production").strip().lower()
    if eval_mode not in {"production", "strict"}:
        eval_mode = "production"

    if not llm_enabled:
        error_msg = "llm_disabled"
    elif not endpoint or not model:
        error_msg = "llm_not_configured: endpoint/model required"
    elif max_llm <= 0:
        error_msg = "llm_not_configured: max_lines_per_scan must be > 0"
    elif analyses:
        try:
            refiner = LLMRefiner(endpoint=endpoint, model=model, api_key=str(llm_cfg.get("api_key", "") or ""))
            subset = analyses[: max_llm]
            refined = refiner.refine_lines(
                subset,
                timeout_ms=timeout_ms,
                temperature=temp,
                eval_mode=eval_mode,
                context=context_cfg if isinstance(context_cfg, dict) else {},
            )
            if not refined:
                error_msg = "llm_invalid_or_empty_response"
        except Exception as exc:
            error_msg = str(exc) or "llm_refine_failed"

    results: List[dict] = []
    meter_normalizations = 0
    token_repairs = 0
    meter_overrides = 0
    dominant_meter = ""
    dominant_ratio = 0.0
    dominant_line_count = 0
    if isinstance(context_cfg, dict):
        dominant_meter = str(context_cfg.get("dominant_meter") or "").strip().lower()
        ctx_ratio = context_cfg.get("dominant_ratio")
        if isinstance(ctx_ratio, (int, float)):
            dominant_ratio = max(0.0, min(1.0, float(ctx_ratio)))
        ctx_count = context_cfg.get("dominant_line_count")
        if isinstance(ctx_count, int):
            dominant_line_count = max(0, ctx_count)
    if not dominant_meter:
        dominant_meter, dominant_ratio, dominant_line_count = _weighted_dominant_meter(refined)

    for a in analyses:
        if error_msg is not None:
            continue
        r = refined.get(a.line_no)
        if r is None:
            continue
        meter_name = getattr(r, "meter_name", "") or ""
        meter_name_llm = meter_name
        meter_name_raw = getattr(r, "meter_name_raw", "") or meter_name
        meter_name_normalized = bool(getattr(r, "meter_name_normalized", False))
        token_repairs_applied = int(getattr(r, "token_repairs_applied", 0) or 0)
        strict_eval_result = bool(getattr(r, "strict_eval", False))
        conf = getattr(r, "confidence", 0.0) or 0.0
        hint = getattr(r, "analysis_hint", "") or ""
        token_patterns = getattr(r, "token_patterns", []) or []
        source = "llm"
        if meter_name_normalized:
            meter_normalizations += 1
        if token_repairs_applied > 0:
            token_repairs += token_repairs_applied

        stress_pattern = "".join(p for p in token_patterns if isinstance(p, str))
        pattern_best_meter, pattern_best_score, pattern_debug = engine.best_meter_for_stress_pattern(stress_pattern)
        pattern_best_margin = float(pattern_debug.get("margin") or 0.0)
        meter_overridden = False
        override_reason = ""

        if (
            pattern_best_meter
            and pattern_best_meter != meter_name
            and pattern_best_score >= RESCORE_MIN_SCORE
            and pattern_best_margin >= RESCORE_MIN_MARGIN
        ):
            meter_name = pattern_best_meter
            conf = min(float(conf), float(pattern_best_score))
            meter_overridden = True
            override_reason = "pattern_rescore"

        can_apply_dominant = (
            dominant_meter
            and dominant_ratio >= DOMINANT_RATIO_MIN
            and dominant_line_count >= DOMINANT_MIN_LINES
            and meter_name != dominant_meter
        )
        if can_apply_dominant:
            dom_score = engine.score_stress_pattern_for_meter(stress_pattern, dominant_meter)
            cur_score = engine.score_stress_pattern_for_meter(stress_pattern, meter_name)
            if (
                dom_score is not None
                and cur_score is not None
                and float(conf) <= DOMINANT_LOW_CONF
                and float(dom_score) >= (float(cur_score) + DOMINANT_SCORE_DELTA)
            ):
                meter_name = dominant_meter
                conf = min(float(conf), float(dom_score))
                meter_overridden = True
                override_reason = "dominant_smoothing"

        if meter_overridden:
            meter_overrides += 1

        spans = _stress_spans_for_line(a.source_text, token_patterns)
        results.append(
            {
                "lnum": int(a.line_no),
                "text": a.source_text,
                "meter_name": meter_name,
                "meter_name_llm": meter_name_llm,
                "meter_name_raw": meter_name_raw,
                "meter_name_normalized": meter_name_normalized,
                "confidence": float(conf),
                "source": source,
                "hint": hint,
                "token_patterns": token_patterns,
                "stress_spans": spans,
                "token_repairs_applied": token_repairs_applied,
                "strict_eval": strict_eval_result,
                "meter_overridden": meter_overridden,
                "override_reason": override_reason,
                "pattern_best_meter": pattern_best_meter,
                "pattern_best_score": float(pattern_best_score),
                "pattern_best_margin": float(pattern_best_margin),
            }
        )

    payload = {
        "results": results,
        "eval": {
            "mode": eval_mode,
            "line_count": len(analyses),
            "result_count": len(results),
            "meter_normalizations": meter_normalizations,
            "token_repairs": token_repairs,
            "meter_overrides": meter_overrides,
            "strict": eval_mode == "strict",
            "dominant_meter": dominant_meter,
            "dominant_ratio": dominant_ratio,
            "dominant_line_count": dominant_line_count,
        },
    }
    if error_msg is not None:
        payload["error"] = error_msg
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
