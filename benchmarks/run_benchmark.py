#!/usr/bin/env python3
"""Run the full MeterMeter pipeline benchmark against annotated corpora.

Primary measurement: full pipeline (deterministic + LLM).
Diagnostic layer: deterministic-only baseline for LLM delta analysis.

Usage:
    python benchmarks/run_benchmark.py [--data-dir DIR] [--no-llm] [--output FILE]
"""
import argparse
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

# Ensure the nvim python path is importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
_NVIM_PY = os.path.join(_HERE, "..", "nvim", "metermeter.nvim", "python")
if _NVIM_PY not in sys.path:
    sys.path.insert(0, _NVIM_PY)

import metermeter_cli
from metermeter.meter_engine import MeterEngine

from parse_4b4v import BenchmarkLine, parse_corpus, corpus_stats


@dataclass
class LineResult:
    """Per-line benchmark result."""
    line: BenchmarkLine
    # Deterministic baseline
    baseline_meter: str = ""
    baseline_stress: str = ""
    baseline_token_patterns: List[str] = field(default_factory=list)
    baseline_confidence: float = 0.0
    baseline_oov_tokens: List[str] = field(default_factory=list)
    baseline_analysis: object = None  # LineAnalysis from MeterEngine; kept for direct LLM calls
    # Full pipeline (with LLM)
    pipeline_meter: str = ""
    pipeline_stress: str = ""
    pipeline_token_patterns: List[str] = field(default_factory=list)
    pipeline_confidence: float = 0.0
    pipeline_overridden: bool = False
    pipeline_override_reason: str = ""
    # Accuracy
    meter_correct_baseline: bool = False
    meter_correct_pipeline: bool = False
    stress_hamming_baseline: int = 0
    stress_hamming_pipeline: int = 0
    stress_accuracy_baseline: float = 0.0
    stress_accuracy_pipeline: float = 0.0
    # LLM delta classification
    llm_delta: str = ""  # "helped", "neutral", "hurt", "both_wrong"
    error_category: str = ""  # scaffolding, llm, override, joint, bias, oov


def _hamming(a: str, b: str) -> Tuple[int, float]:
    """Hamming distance and accuracy between two stress patterns."""
    if not a or not b:
        return max(len(a), len(b)), 0.0
    common = min(len(a), len(b))
    matches = sum(1 for i in range(common) if a[i] == b[i])
    dist = (common - matches) + abs(len(a) - len(b))
    total = max(len(a), len(b))
    accuracy = matches / total if total > 0 else 0.0
    return dist, accuracy


def _stress_f1(predicted: str, gold: str, target: str = "S") -> Dict[str, float]:
    """Compute precision, recall, F1 for a specific stress symbol."""
    common = min(len(predicted), len(gold))
    tp = sum(1 for i in range(common) if predicted[i] == target and gold[i] == target)
    fp = sum(1 for i in range(common) if predicted[i] == target and gold[i] != target)
    fn = sum(1 for i in range(common) if predicted[i] != target and gold[i] == target)
    # Count length-mismatch positions as misses
    if len(predicted) > common:
        fp += sum(1 for i in range(common, len(predicted)) if predicted[i] == target)
    if len(gold) > common:
        fn += sum(1 for i in range(common, len(gold)) if gold[i] == target)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def _classify_llm_delta(
    baseline_meter_correct: bool,
    pipeline_meter_correct: bool,
    baseline_stress_acc: float,
    pipeline_stress_acc: float,
) -> str:
    """Classify LLM contribution for a single line."""
    if pipeline_meter_correct and not baseline_meter_correct:
        return "helped"
    if not pipeline_meter_correct and baseline_meter_correct:
        return "hurt"
    if pipeline_meter_correct and baseline_meter_correct:
        if pipeline_stress_acc > baseline_stress_acc + 0.05:
            return "helped"
        if pipeline_stress_acc < baseline_stress_acc - 0.05:
            return "hurt"
        return "neutral"
    # Both wrong
    return "both_wrong"


def _classify_error(result: LineResult) -> str:
    """Classify root cause of pipeline error."""
    if result.meter_correct_pipeline:
        return ""
    if result.baseline_oov_tokens:
        oov_ratio = len(result.baseline_oov_tokens) / max(
            1, len(result.baseline_token_patterns)
        )
        if oov_ratio > 0.3:
            return "oov"
    if result.pipeline_overridden:
        if result.llm_delta == "hurt":
            return "override"
    if result.meter_correct_baseline and not result.meter_correct_pipeline:
        return "llm"
    if not result.meter_correct_baseline and not result.meter_correct_pipeline:
        if result.stress_accuracy_baseline < 0.5:
            return "scaffolding"
        return "joint"
    if result.baseline_meter.startswith("iambic") and not result.line.gold_meter.startswith("iambic"):
        return "bias"
    return "scaffolding"


def run_deterministic(engine: MeterEngine, lines: List[BenchmarkLine]) -> Dict[int, LineResult]:
    """Run deterministic-only baseline on all lines."""
    results: Dict[int, LineResult] = {}
    for i, line in enumerate(lines):
        r = LineResult(line=line)
        analysis = engine.analyze_line(line.text, line_no=i)
        if analysis:
            r.baseline_analysis = analysis
            r.baseline_meter = analysis.meter_name
            r.baseline_stress = analysis.stress_pattern
            r.baseline_token_patterns = list(analysis.token_patterns)
            r.baseline_confidence = analysis.confidence
            r.baseline_oov_tokens = list(analysis.oov_tokens)

            gold = line.gold_meter.strip().lower()
            r.meter_correct_baseline = r.baseline_meter.strip().lower() == gold

            dist, acc = _hamming(r.baseline_stress, line.gold_stress)
            r.stress_hamming_baseline = dist
            r.stress_accuracy_baseline = acc
        results[i] = r
    return results


def _compute_gold_poem_contexts(lines: List[BenchmarkLine]) -> Dict[str, dict]:
    """Compute per-poem contexts using gold meter labels (upper bound for context quality)."""
    from collections import Counter
    poem_meters: Dict[str, Counter] = {}
    for line in lines:
        if not line.gold_meter:
            continue
        poem = line.poem_file
        if poem not in poem_meters:
            poem_meters[poem] = Counter()
        poem_meters[poem][line.gold_meter.strip().lower()] += 1

    contexts: Dict[str, dict] = {}
    for poem, counter in poem_meters.items():
        total = sum(counter.values())
        if total == 0:
            continue
        dominant, count = counter.most_common(1)[0]
        contexts[poem] = {
            "dominant_meter": dominant,
            "dominant_ratio": count / total,
            "dominant_line_count": total,
        }
    return contexts


def _compute_poem_contexts(lines: List[BenchmarkLine], results: Dict[int, LineResult]) -> Dict[str, dict]:
    """Compute per-poem dominant meter context from baseline results."""
    poem_meter_weights: Dict[str, Dict[str, float]] = {}
    for i, line in enumerate(lines):
        r = results.get(i)
        if not r or not r.baseline_meter:
            continue
        poem = line.poem_file
        meter = r.baseline_meter.strip().lower()
        weight = max(0.05, float(r.baseline_confidence))
        if poem not in poem_meter_weights:
            poem_meter_weights[poem] = {}
        poem_meter_weights[poem][meter] = poem_meter_weights[poem].get(meter, 0.0) + weight

    contexts: Dict[str, dict] = {}
    for poem, weights in poem_meter_weights.items():
        total = sum(weights.values())
        if total <= 0:
            continue
        dominant = max(weights, key=lambda k: weights[k])
        contexts[poem] = {
            "dominant_meter": dominant,
            "dominant_ratio": weights[dominant] / total,
            "dominant_line_count": len([l for l in lines if l.poem_file == poem]),
        }
    return contexts


def _process_batch_output(out: dict, results: Dict[int, LineResult]) -> None:
    """Update results in place from a single pipeline batch output dict."""
    for item in out.get("results") or []:
        if not isinstance(item, dict):
            continue
        lnum = item.get("lnum")
        if not isinstance(lnum, int) or lnum not in results:
            continue
        r = results[lnum]
        r.pipeline_meter = str(item.get("meter_name", ""))
        r.pipeline_stress = "".join(str(p) for p in (item.get("token_patterns") or []))
        r.pipeline_token_patterns = list(item.get("token_patterns") or [])
        r.pipeline_confidence = float(item.get("confidence", 0.0))
        r.pipeline_overridden = bool(item.get("meter_overridden", False))
        r.pipeline_override_reason = str(item.get("override_reason", ""))

        gold = r.line.gold_meter.strip().lower()
        r.meter_correct_pipeline = r.pipeline_meter.strip().lower() == gold

        dist, acc = _hamming(r.pipeline_stress, r.line.gold_stress)
        r.stress_hamming_pipeline = dist
        r.stress_accuracy_pipeline = acc

        r.llm_delta = _classify_llm_delta(
            r.meter_correct_baseline,
            r.meter_correct_pipeline,
            r.stress_accuracy_baseline,
            r.stress_accuracy_pipeline,
        )
        r.error_category = _classify_error(r)


def _send_batch(
    batch_lines: List[dict],
    endpoint: str,
    model: str,
    timeout_ms: int,
    temperature: float,
    context_cfg: dict,
) -> Optional[dict]:
    """Send a single batch to the CLI pipeline and return the parsed output dict."""
    req = {
        "config": {
            "llm": {
                "enabled": True,
                "endpoint": endpoint,
                "model": model,
                "timeout_ms": timeout_ms,
                "temperature": temperature,
                "max_lines_per_scan": len(batch_lines),
                "eval_mode": "production",
            },
            "context": context_cfg,
        },
        "lines": batch_lines,
    }
    stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
    stdout = io.StringIO()
    with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
        metermeter_cli.main()
    return json.loads(stdout.getvalue() or "{}")


def run_pipeline(
    lines: List[BenchmarkLine],
    results: Dict[int, LineResult],
    endpoint: str,
    model: str,
    batch_size: int = 2,
    timeout_ms: int = 60000,
    temperature: float = 0.0,
    progress: bool = False,
    poem_contexts: Optional[Dict[str, dict]] = None,
) -> None:
    """Run the full pipeline (with LLM) and update results in place."""
    total = len(lines)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch_lines = [
            {"lnum": i, "text": lines[i].text}
            for i in range(start, end)
        ]
        # Use context from the poem of the first line in this batch.
        context_cfg: dict = {}
        if poem_contexts:
            first_poem = lines[start].poem_file
            context_cfg = poem_contexts.get(first_poem, {})
        try:
            out = _send_batch(batch_lines, endpoint, model, timeout_ms, temperature, context_cfg)
        except Exception as exc:
            if progress:
                print(f"  batch {start}-{end}: error: {exc}", file=sys.stderr)
            continue

        if progress:
            n_results = len(out.get("results") or [])
            print(f"  batch {start+1}-{end}/{total}: {n_results} results", file=sys.stderr)

        _process_batch_output(out, results)


def _refine_poem(
    analyses: list,
    refiner: object,
    engine: object,
    timeout_ms: int,
    temperature: float,
    override_fns: Optional[list] = None,
) -> Tuple[Dict[int, dict], Optional[str]]:
    """Refine one poem's analyses with the LLM and apply overrides. Returns per-line update dicts."""
    from metermeter.llm_refiner import LLMRefiner as _LR  # type: ignore
    # 10s base (prompt overhead) + per-line budget.
    effective_timeout = 10000 + len(analyses) * timeout_ms
    try:
        refined = refiner.refine_lines(analyses, timeout_ms=effective_timeout, temperature=temperature)  # type: ignore
    except Exception as exc:
        return {}, str(exc)

    # Prefer LLM-reported dominant meter to break the bias feedback loop.
    llm_dominant = str(getattr(refiner, "last_dominant_meter", "") or "").strip().lower()
    if llm_dominant:
        dominant_meter = llm_dominant
        # Estimate ratio from how many lines the LLM classified as the dominant meter.
        # Use a floor of 0.80 because the LLM's holistic judgment (dominant_meter field)
        # is more reliable than its per-line classifications, which suffer from
        # trochaic mislabeling that artificially suppresses the ratio.
        match_count = sum(
            1 for r in refined.values()
            if str(getattr(r, "meter_name", "") or "").strip().lower() == llm_dominant
        )
        per_line_ratio = match_count / len(refined) if refined else 0.0
        dominant_ratio = max(per_line_ratio, 0.80)
        dominant_line_count = len(refined)
    else:
        dominant_meter, dominant_ratio, dominant_line_count = metermeter_cli._weighted_dominant_meter(refined)

    if override_fns is None:
        override_fns = list(metermeter_cli._METER_OVERRIDES)

    updates: Dict[int, dict] = {}
    for a in analyses:
        r_llm = refined.get(a.line_no)
        if r_llm is None:
            continue
        meter_name = str(getattr(r_llm, "meter_name", "") or "")
        conf = float(getattr(r_llm, "confidence", 0.0) or 0.0)
        token_patterns = list(getattr(r_llm, "token_patterns", []) or [])

        stress_pattern = "".join(p for p in token_patterns if isinstance(p, str))
        pattern_best_meter, pattern_best_score, pattern_debug = engine.best_meter_for_stress_pattern(stress_pattern)  # type: ignore
        pattern_best_margin = pattern_debug.get("margin") or 0.0
        baseline_meter = (a.meter_name or "").strip().lower()
        baseline_conf = float(getattr(a, "confidence", 0.0) or 0.0)

        override_ctx = dict(
            meter_name=meter_name, conf=conf,
            pattern_best_meter=pattern_best_meter,
            pattern_best_score=pattern_best_score,
            pattern_best_margin=pattern_best_margin,
            stress_pattern=stress_pattern,
            baseline_meter=baseline_meter, baseline_conf=baseline_conf,
            dominant_meter=dominant_meter, dominant_ratio=dominant_ratio,
            dominant_line_count=dominant_line_count, engine=engine,
            has_precomputed_context=False,
        )
        meter_overridden = False
        override_reason = ""
        for override_fn in override_fns:
            ov = override_fn(**override_ctx)
            if ov is not None:
                meter_name, conf, override_reason = ov
                meter_overridden = True
                break

        updates[a.line_no] = dict(
            pipeline_meter=meter_name,
            pipeline_stress=stress_pattern,
            pipeline_token_patterns=token_patterns,
            pipeline_confidence=conf,
            pipeline_overridden=meter_overridden,
            pipeline_override_reason=override_reason,
        )
    return updates, None


def run_pipeline_poem_batch(
    lines: List[BenchmarkLine],
    results: Dict[int, LineResult],
    endpoint: str,
    model: str,
    max_poem_lines: int = 50,
    timeout_ms: int = 3000,
    temperature: float = 0.0,
    workers: int = 1,
    override_fns: Optional[list] = None,
) -> None:
    """Run pipeline with poem-level batching: each poem sent as one LLM call, no pre-computed context.

    Bypasses the CLI to avoid re-running prosodic on already-computed baselines.
    Workers > 1 sends poems to Ollama in parallel (requires Ollama --parallel support).
    """
    import concurrent.futures
    from collections import defaultdict
    from metermeter.llm_refiner import LLMRefiner
    from metermeter.meter_engine import MeterEngine as _ME
    from tqdm import tqdm

    refiner = LLMRefiner(endpoint=endpoint, model=model)
    engine = _ME()

    # Group line indices by poem.
    poem_indices: Dict[str, List[int]] = defaultdict(list)
    for i, line in enumerate(lines):
        poem_indices[line.poem_file].append(i)

    # Build work items: (poem_file, chunk_indices) for each chunk.
    work: List[Tuple[str, List[int]]] = []
    for poem_file, indices in poem_indices.items():
        for chunk in [indices[s:s + max_poem_lines] for s in range(0, len(indices), max_poem_lines)]:
            work.append((poem_file, chunk))

    with tqdm(total=len(poem_indices), unit="poem", dynamic_ncols=True) as pbar:
        def submit_and_track(executor):
            futures = {}
            for poem_file, chunk_indices in work:
                analyses = [results[i].baseline_analysis for i in chunk_indices
                            if results[i].baseline_analysis is not None]
                if analyses:
                    fut = executor.submit(_refine_poem, analyses, refiner, engine, timeout_ms, temperature, override_fns)
                    futures[fut] = (poem_file, len(chunk_indices))
            return futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = submit_and_track(executor)
            seen_poems: set = set()
            for fut in concurrent.futures.as_completed(futures):
                poem_file, n_lines = futures[fut]
                updates, err = fut.result()
                if err:
                    tqdm.write(f"  skip {poem_file} ({n_lines} lines): {err}")
                else:
                    # Apply updates in the main thread (no locking needed: disjoint keys per poem).
                    for line_no, data in updates.items():
                        lr = results[line_no]
                        for k, v in data.items():
                            setattr(lr, k, v)
                        gold = lr.line.gold_meter.strip().lower()
                        lr.meter_correct_pipeline = lr.pipeline_meter.strip().lower() == gold
                        dist, acc = _hamming(lr.pipeline_stress, lr.line.gold_stress)
                        lr.stress_hamming_pipeline = dist
                        lr.stress_accuracy_pipeline = acc
                        lr.llm_delta = _classify_llm_delta(
                            lr.meter_correct_baseline, lr.meter_correct_pipeline,
                            lr.stress_accuracy_baseline, lr.stress_accuracy_pipeline,
                        )
                        lr.error_category = _classify_error(lr)

                if poem_file not in seen_poems:
                    seen_poems.add(poem_file)
                    pbar.update(1)
                    pbar.set_postfix(poem=poem_file[:35], lines=n_lines,
                                     hits=len(updates) if not err else 0)


def compile_report(results: Dict[int, LineResult], include_llm: bool = True) -> Dict[str, object]:
    """Compile a full benchmark report from line results."""
    total = len(results)
    if total == 0:
        return {"error": "no results"}

    # Meter accuracy — pipeline only counts lines where the LLM actually returned a result.
    # Lines skipped due to timeout/error retain empty pipeline_meter and are excluded from
    # the pipeline denominator so they don't inflate the error rate.
    baseline_meter_correct = sum(1 for r in results.values() if r.meter_correct_baseline)
    pipeline_lines = [r for r in results.values() if r.pipeline_meter] if include_llm else []
    pipeline_total = len(pipeline_lines)
    pipeline_meter_correct = sum(1 for r in pipeline_lines if r.meter_correct_pipeline) if include_llm else 0

    # Stress accuracy
    baseline_stress_accs = [r.stress_accuracy_baseline for r in results.values()]
    pipeline_stress_accs = [r.stress_accuracy_pipeline for r in pipeline_lines] if include_llm else []

    # Stress F1 (aggregate)
    all_baseline_stress = "".join(r.baseline_stress for r in results.values())
    all_gold_stress = "".join(r.line.gold_stress for r in results.values())
    baseline_f1 = _stress_f1(all_baseline_stress, all_gold_stress)

    all_pipeline_stress = "".join(r.pipeline_stress for r in results.values()) if include_llm else ""
    pipeline_f1 = _stress_f1(all_pipeline_stress, all_gold_stress) if include_llm else {}

    # OOV rate
    total_tokens = sum(max(1, len(r.baseline_token_patterns)) for r in results.values())
    total_oov = sum(len(r.baseline_oov_tokens) for r in results.values())

    # Confusion matrix (meter types)
    confusion: Dict[str, Dict[str, int]] = {}
    for r in results.values():
        gold = r.line.gold_meter.strip().lower()
        pred = (r.pipeline_meter if include_llm else r.baseline_meter).strip().lower()
        if gold not in confusion:
            confusion[gold] = {}
        confusion[gold][pred] = confusion[gold].get(pred, 0) + 1

    # LLM delta breakdown
    llm_deltas: Dict[str, int] = {}
    error_cats: Dict[str, int] = {}
    if include_llm:
        for r in results.values():
            llm_deltas[r.llm_delta] = llm_deltas.get(r.llm_delta, 0) + 1
            if r.error_category:
                error_cats[r.error_category] = error_cats.get(r.error_category, 0) + 1

    # Breakdown by meter type
    by_meter: Dict[str, Dict[str, object]] = {}
    for r in results.values():
        gold = r.line.gold_meter.strip().lower()
        if gold not in by_meter:
            by_meter[gold] = {"count": 0, "baseline_correct": 0, "pipeline_correct": 0}
        by_meter[gold]["count"] += 1
        if r.meter_correct_baseline:
            by_meter[gold]["baseline_correct"] += 1
        if include_llm and r.meter_correct_pipeline:
            by_meter[gold]["pipeline_correct"] += 1

    # Breakdown by century
    by_century: Dict[str, Dict[str, object]] = {}
    for r in results.values():
        c = r.line.century or "unknown"
        if c not in by_century:
            by_century[c] = {"count": 0, "baseline_correct": 0, "pipeline_correct": 0}
        by_century[c]["count"] += 1
        if r.meter_correct_baseline:
            by_century[c]["baseline_correct"] += 1
        if include_llm and r.meter_correct_pipeline:
            by_century[c]["pipeline_correct"] += 1

    # Per-line errors (pipeline failures)
    error_lines: List[Dict[str, object]] = []
    source = "pipeline" if include_llm else "baseline"
    for i in sorted(results.keys()):
        r = results[i]
        correct = r.meter_correct_pipeline if include_llm else r.meter_correct_baseline
        if correct:
            continue
        pred_meter = r.pipeline_meter if include_llm else r.baseline_meter
        pred_stress = r.pipeline_stress if include_llm else r.baseline_stress
        stress_acc = r.stress_accuracy_pipeline if include_llm else r.stress_accuracy_baseline
        error_lines.append({
            "line_num": i + 1,
            "poem": r.line.poem_file,
            "text": r.line.text,
            "gold_meter": r.line.gold_meter,
            "predicted_meter": pred_meter,
            "gold_stress": r.line.gold_stress,
            "predicted_stress": pred_stress,
            "stress_accuracy": round(stress_acc, 3),
            "error_category": r.error_category,
            "llm_delta": r.llm_delta,
            "oov_tokens": r.baseline_oov_tokens,
        })

    report = {
        "total_lines": total,
        "baseline_meter_accuracy": round(baseline_meter_correct / total, 4),
        "baseline_stress_accuracy_mean": round(sum(baseline_stress_accs) / len(baseline_stress_accs), 4) if baseline_stress_accs else 0.0,
        "baseline_stress_f1": {k: round(v, 4) if isinstance(v, float) else v for k, v in baseline_f1.items()},
        "oov_rate": round(total_oov / total_tokens, 4) if total_tokens > 0 else 0.0,
        "confusion_matrix": confusion,
        "by_meter": by_meter,
        "by_century": by_century,
    }

    if include_llm:
        report.update({
            "pipeline_lines": pipeline_total,
            "pipeline_coverage": round(pipeline_total / total, 4) if total else 0.0,
            "pipeline_meter_accuracy": round(pipeline_meter_correct / pipeline_total, 4) if pipeline_total else 0.0,
            "pipeline_stress_accuracy_mean": round(sum(pipeline_stress_accs) / len(pipeline_stress_accs), 4) if pipeline_stress_accs else 0.0,
            "pipeline_stress_f1": {k: round(v, 4) if isinstance(v, float) else v for k, v in pipeline_f1.items()},
            "llm_delta": llm_deltas,
            "error_categories": error_cats,
        })

    report["error_lines"] = error_lines[:50]  # Cap to avoid huge output
    report["total_errors"] = len(error_lines)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MeterMeter benchmark")
    parser.add_argument("--data-dir", default=os.path.join(_HERE, "data", "poems"),
                        help="Path to 4B4V poems directory")
    parser.add_argument("--no-llm", action="store_true",
                        help="Run deterministic baseline only (no LLM)")
    parser.add_argument("--endpoint", default=os.environ.get(
        "METERMETER_LLM_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions"))
    parser.add_argument("--model", default=os.environ.get(
        "METERMETER_LLM_MODEL", "qwen2.5:7b-instruct"))
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--timeout-ms", type=int, default=3000,
                        help="Per-line LLM budget in ms (default 3000); actual timeout = 10s + N*budget, "
                             "so a 14-line poem gets 52s")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-lines", type=int, default=0,
                        help="Limit corpus to first N lines (0 = all)")
    parser.add_argument("--max-poems", type=int, default=0,
                        help="Limit to first N poems (0 = all); useful for quick checks")
    parser.add_argument("--gold-context", action="store_true",
                        help="Pass gold poem meter as dominant_meter context (upper bound)")
    parser.add_argument("--poem-batch", action="store_true",
                        help="Send each poem as a single LLM batch with no pre-computed context; "
                             "LLM determines dominant meter holistically")
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel LLM workers for --poem-batch (default 1; set >1 if Ollama runs with --parallel)")
    parser.add_argument("--no-overrides", action="store_true",
                        help="Skip all _METER_OVERRIDES in poem-batch mode (measure raw LLM accuracy)")
    parser.add_argument("--output", default="", help="Write JSON report to file")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    lines = parse_corpus(args.data_dir)
    if args.max_poems > 0:
        seen: list = []
        poem_count = 0
        prev = None
        for l in lines:
            if l.poem_file != prev:
                poem_count += 1
                prev = l.poem_file
            if poem_count > args.max_poems:
                break
            seen.append(l)
        lines = seen
    elif args.max_lines > 0:
        lines = lines[: args.max_lines]
    if not lines:
        print(f"No annotated lines found in {args.data_dir}", file=sys.stderr)
        print("See benchmarks/data/.gitignore for setup instructions.", file=sys.stderr)
        return 1

    stats = corpus_stats(lines)
    if args.progress:
        print(f"Corpus: {stats['total_lines']} lines from {stats['total_poems']} poems", file=sys.stderr)

    import contextlib
    engine = MeterEngine()
    t0 = time.time()
    # Suppress prosodic's per-line parsing noise; it completes in a few seconds anyway.
    with open(os.devnull, "w") as _null, contextlib.redirect_stderr(_null):
        results = run_deterministic(engine, lines)
    baseline_time = time.time() - t0

    if args.progress:
        baseline_acc = sum(1 for r in results.values() if r.meter_correct_baseline) / len(results)
        print(f"Baseline: {baseline_acc:.1%} in {baseline_time:.1f}s ({len(results)} lines)", file=sys.stderr)

    include_llm = not args.no_llm
    if include_llm:
        t1 = time.time()
        if args.poem_batch:
            poem_override_fns = [] if args.no_overrides else None
            run_pipeline_poem_batch(
                lines, results,
                endpoint=args.endpoint,
                model=args.model,
                timeout_ms=args.timeout_ms,
                temperature=args.temperature,
                workers=args.workers,
                override_fns=poem_override_fns,
            )
        else:
            if args.gold_context:
                poem_contexts = _compute_gold_poem_contexts(lines)
                context_label = "gold"
            else:
                poem_contexts = _compute_poem_contexts(lines, results)
                context_label = "baseline"
            if args.progress:
                print(f"Running full pipeline with LLM ({context_label} context for {len(poem_contexts)} poems)...", file=sys.stderr)
            run_pipeline(
                lines, results,
                endpoint=args.endpoint,
                model=args.model,
                batch_size=args.batch_size,
                timeout_ms=args.timeout_ms,
                temperature=args.temperature,
                progress=args.progress,
                poem_contexts=poem_contexts,
            )
        pipeline_time = time.time() - t1
        if args.progress:
            pipeline_lines = [r for r in results.values() if r.pipeline_meter]
            pipeline_acc = sum(1 for r in pipeline_lines if r.meter_correct_pipeline) / len(pipeline_lines) if pipeline_lines else 0.0
            coverage = len(pipeline_lines) / len(results) if results else 0.0
            print(f"Pipeline: {pipeline_acc:.1%} meter accuracy ({coverage:.0%} coverage) in {pipeline_time:.1f}s", file=sys.stderr)

    report = compile_report(results, include_llm=include_llm)
    report["corpus_stats"] = stats
    report["timing"] = {
        "baseline_seconds": round(baseline_time, 2),
    }
    if include_llm:
        report["timing"]["pipeline_seconds"] = round(pipeline_time, 2)

    output = json.dumps(report, indent=2, ensure_ascii=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output)
        if args.progress:
            print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
