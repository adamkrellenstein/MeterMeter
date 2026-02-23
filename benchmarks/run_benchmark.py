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


def run_pipeline(
    lines: List[BenchmarkLine],
    results: Dict[int, LineResult],
    endpoint: str,
    model: str,
    batch_size: int = 2,
    timeout_ms: int = 60000,
    temperature: float = 0.0,
    progress: bool = False,
) -> None:
    """Run the full pipeline (with LLM) and update results in place."""
    total = len(lines)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch_lines = [
            {"lnum": i, "text": lines[i].text}
            for i in range(start, end)
        ]
        req = {
            "config": {
                "llm": {
                    "enabled": True,
                    "endpoint": endpoint,
                    "model": model,
                    "timeout_ms": timeout_ms,
                    "temperature": temperature,
                    "max_lines_per_scan": batch_size,
                    "eval_mode": "production",
                },
                "lexicon_path": "",
            },
            "lines": batch_lines,
        }
        stdin = io.StringIO(json.dumps(req, ensure_ascii=True))
        stdout = io.StringIO()
        try:
            with patch("sys.stdin", stdin), patch("sys.stdout", stdout):
                metermeter_cli.main()
            out = json.loads(stdout.getvalue() or "{}")
        except Exception as exc:
            if progress:
                print(f"  batch {start}-{end}: error: {exc}", file=sys.stderr)
            continue

        if progress:
            n_results = len(out.get("results") or [])
            print(f"  batch {start+1}-{end}/{total}: {n_results} results", file=sys.stderr)

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


def compile_report(results: Dict[int, LineResult], include_llm: bool = True) -> Dict[str, object]:
    """Compile a full benchmark report from line results."""
    total = len(results)
    if total == 0:
        return {"error": "no results"}

    # Meter accuracy
    baseline_meter_correct = sum(1 for r in results.values() if r.meter_correct_baseline)
    pipeline_meter_correct = sum(1 for r in results.values() if r.meter_correct_pipeline) if include_llm else 0

    # Stress accuracy
    baseline_stress_accs = [r.stress_accuracy_baseline for r in results.values()]
    pipeline_stress_accs = [r.stress_accuracy_pipeline for r in results.values()] if include_llm else []

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
            "pipeline_meter_accuracy": round(pipeline_meter_correct / total, 4),
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
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--output", default="", help="Write JSON report to file")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    lines = parse_corpus(args.data_dir)
    if not lines:
        print(f"No annotated lines found in {args.data_dir}", file=sys.stderr)
        print("See benchmarks/data/.gitignore for setup instructions.", file=sys.stderr)
        return 1

    stats = corpus_stats(lines)
    if args.progress:
        print(f"Corpus: {stats['total_lines']} lines from {stats['total_poems']} poems", file=sys.stderr)

    engine = MeterEngine()
    t0 = time.time()
    results = run_deterministic(engine, lines)
    baseline_time = time.time() - t0

    if args.progress:
        baseline_acc = sum(1 for r in results.values() if r.meter_correct_baseline) / len(results)
        print(f"Baseline: {baseline_acc:.1%} meter accuracy in {baseline_time:.1f}s", file=sys.stderr)

    include_llm = not args.no_llm
    if include_llm:
        if args.progress:
            print("Running full pipeline with LLM...", file=sys.stderr)
        t1 = time.time()
        run_pipeline(
            lines, results,
            endpoint=args.endpoint,
            model=args.model,
            batch_size=args.batch_size,
            timeout_ms=args.timeout_ms,
            temperature=args.temperature,
            progress=args.progress,
        )
        pipeline_time = time.time() - t1
        if args.progress:
            pipeline_acc = sum(1 for r in results.values() if r.meter_correct_pipeline) / len(results)
            print(f"Pipeline: {pipeline_acc:.1%} meter accuracy in {pipeline_time:.1f}s", file=sys.stderr)

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
