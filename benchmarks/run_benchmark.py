#!/usr/bin/env python3
"""Run the MeterMeter deterministic benchmark against annotated corpora.

Usage:
    python benchmarks/run_benchmark.py [--data-dir DIR] [--output FILE]
"""
import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# Ensure the nvim python path is importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
_NVIM_PY = os.path.join(_HERE, "..", "nvim", "metermeter.nvim", "python")
if _NVIM_PY not in sys.path:
    sys.path.insert(0, _NVIM_PY)

from metermeter.meter_engine import MeterEngine

from parse_4b4v import BenchmarkLine, parse_corpus, corpus_stats


@dataclass
class LineResult:
    """Per-line benchmark result."""
    line: BenchmarkLine
    baseline_meter: str = ""
    baseline_stress: str = ""
    baseline_token_patterns: List[str] = field(default_factory=list)
    baseline_confidence: float = 0.0
    meter_correct: bool = False
    stress_hamming: int = 0
    stress_accuracy: float = 0.0


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
    if len(predicted) > common:
        fp += sum(1 for i in range(common, len(predicted)) if predicted[i] == target)
    if len(gold) > common:
        fn += sum(1 for i in range(common, len(gold)) if gold[i] == target)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def run_deterministic(engine: MeterEngine, lines: List[BenchmarkLine]) -> Dict[int, LineResult]:
    """Run deterministic baseline on all lines."""
    results: Dict[int, LineResult] = {}
    for i, line in enumerate(lines):
        r = LineResult(line=line)
        analysis = engine.analyze_line(line.text, line_no=i)
        if analysis:
            r.baseline_meter = analysis.meter_name
            r.baseline_stress = analysis.stress_pattern
            r.baseline_token_patterns = list(analysis.token_patterns)
            r.baseline_confidence = analysis.confidence

            gold = line.gold_meter.strip().lower()
            r.meter_correct = r.baseline_meter.strip().lower() == gold

            dist, acc = _hamming(r.baseline_stress, line.gold_stress)
            r.stress_hamming = dist
            r.stress_accuracy = acc
        results[i] = r
    return results


def compile_report(results: Dict[int, LineResult]) -> Dict[str, object]:
    """Compile a benchmark report from line results."""
    total = len(results)
    if total == 0:
        return {"error": "no results"}

    meter_correct = sum(1 for r in results.values() if r.meter_correct)
    stress_accs = [r.stress_accuracy for r in results.values()]
    stress_exact = sum(1 for r in results.values() if r.baseline_stress and r.baseline_stress == r.line.gold_stress)

    # Stress F1 (aggregate)
    all_stress = "".join(r.baseline_stress for r in results.values())
    all_gold_stress = "".join(r.line.gold_stress for r in results.values())
    stress_f1 = _stress_f1(all_stress, all_gold_stress)

    # Confusion matrix (meter types)
    confusion: Dict[str, Dict[str, int]] = {}
    for r in results.values():
        gold = r.line.gold_meter.strip().lower()
        pred = r.baseline_meter.strip().lower()
        if gold not in confusion:
            confusion[gold] = {}
        confusion[gold][pred] = confusion[gold].get(pred, 0) + 1

    # Breakdown by meter type
    by_meter: Dict[str, Dict[str, object]] = {}
    for r in results.values():
        gold = r.line.gold_meter.strip().lower()
        if gold not in by_meter:
            by_meter[gold] = {"count": 0, "correct": 0}
        by_meter[gold]["count"] += 1
        if r.meter_correct:
            by_meter[gold]["correct"] += 1

    # Breakdown by century
    by_century: Dict[str, Dict[str, object]] = {}
    for r in results.values():
        c = r.line.century or "unknown"
        if c not in by_century:
            by_century[c] = {"count": 0, "correct": 0}
        by_century[c]["count"] += 1
        if r.meter_correct:
            by_century[c]["correct"] += 1

    # Per-line errors
    error_lines: List[Dict[str, object]] = []
    for i in sorted(results.keys()):
        r = results[i]
        if r.meter_correct:
            continue
        error_lines.append({
            "line_num": i + 1,
            "poem": r.line.poem_file,
            "text": r.line.text,
            "gold_meter": r.line.gold_meter,
            "predicted_meter": r.baseline_meter,
            "gold_stress": r.line.gold_stress,
            "predicted_stress": r.baseline_stress,
            "stress_accuracy": round(r.stress_accuracy, 3),
        })

    return {
        "total_lines": total,
        "meter_accuracy": round(meter_correct / total, 4),
        "stress_accuracy_mean": round(sum(stress_accs) / len(stress_accs), 4) if stress_accs else 0.0,
        "stress_exact_line_match_rate": round(stress_exact / total, 4),
        "stress_f1": {k: round(v, 4) if isinstance(v, float) else v for k, v in stress_f1.items()},
        "confusion_matrix": confusion,
        "by_meter": by_meter,
        "by_century": by_century,
        "error_lines": error_lines[:50],
        "total_errors": len(error_lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MeterMeter benchmark")
    parser.add_argument("--data-dir", default=os.path.join(_HERE, "data", "poems"),
                        help="Path to 4B4V poems directory")
    parser.add_argument("--max-lines", type=int, default=0,
                        help="Limit corpus to first N lines (0 = all)")
    parser.add_argument("--max-poems", type=int, default=0,
                        help="Limit to first N poems (0 = all); useful for quick checks")
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

    engine = MeterEngine()
    t0 = time.time()
    # Suppress prosodic's per-line parsing noise.
    with open(os.devnull, "w") as _null, contextlib.redirect_stderr(_null):
        results = run_deterministic(engine, lines)
    elapsed = time.time() - t0

    if args.progress:
        acc = sum(1 for r in results.values() if r.meter_correct) / len(results)
        print(f"Baseline: {acc:.1%} in {elapsed:.1f}s ({len(results)} lines)", file=sys.stderr)

    report = compile_report(results)
    report["corpus_stats"] = stats
    report["timing"] = {"seconds": round(elapsed, 2)}

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
