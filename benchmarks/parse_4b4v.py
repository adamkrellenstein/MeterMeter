#!/usr/bin/env python3
"""Parse the For Better For Verse (4B4V) TEI XML corpus into benchmark records.

Each annotated line in the 4B4V corpus is encoded as a <l> element with:
  - met="..." : the expected metrical template using +/- for S/U
  - real="..." : the actual stress realization (may differ due to substitutions)

This parser extracts (line_text, gold_stress_pattern, gold_meter) tuples suitable
for benchmarking the MeterMeter pipeline.
"""
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Ensure the nvim python path is importable so we can reuse canonical constants.
_HERE = os.path.dirname(os.path.abspath(__file__))
_NVIM_PY = os.path.join(_HERE, "..", "nvim", "metermeter.nvim", "python")
if _NVIM_PY not in sys.path:
    sys.path.insert(0, _NVIM_PY)

from metermeter.meter_engine import FOOT_TEMPLATES, LINE_NAME_BY_FEET as LINE_NAMES

# TEI namespace used by 4B4V XML files.
TEI_NS = "http://www.tei-c.org/ns/1.0"

# 4B4V encodes stress as + (stressed) and - (unstressed).
_STRESS_MAP = {"+": "S", "-": "U"}


@dataclass
class BenchmarkLine:
    """A single annotated line from the 4B4V corpus."""
    poem_file: str
    line_number: int
    text: str
    gold_stress: str       # U/S pattern from "real" attribute
    gold_template: str     # U/S pattern from "met" attribute
    gold_meter: str        # e.g. "iambic pentameter"
    century: str           # from TEI header if available


def _convert_stress(raw: str) -> str:
    """Convert 4B4V +/- stress encoding to U/S.

    Some lines have alternative readings separated by '|'; use only the first.
    """
    primary = raw.split("|")[0]
    return "".join(_STRESS_MAP.get(ch, "") for ch in primary)


def _infer_meter(template: str) -> str:
    """Infer meter name from a U/S template pattern.

    Tries each foot type and picks the best match by residual.
    """
    if not template:
        return ""
    n = len(template)

    best_name = ""
    best_dist = float("inf")

    for foot_name, foot in FOOT_TEMPLATES.items():
        unit = len(foot)
        feet = round(n / unit)
        if feet < 1 or feet > 6:
            continue
        expanded = foot * feet
        # Hamming distance allowing length mismatch
        common = min(len(expanded), n)
        dist = sum(1 for i in range(common) if expanded[i] != template[i])
        dist += abs(len(expanded) - n)
        if dist < best_dist:
            best_dist = dist
            best_name = foot_name
            best_feet = feet

    if not best_name:
        return ""
    line_name = LINE_NAMES.get(best_feet, f"{best_feet}-foot")
    return f"{best_name} {line_name}"


def _extract_century(root: ET.Element) -> str:
    """Try to extract century from TEI header date elements."""
    for tag in ["date", f"{{{TEI_NS}}}date"]:
        for el in root.iter(tag):
            text = (el.text or "").strip()
            when = el.get("when", "").strip()
            for candidate in [when, text]:
                m = re.search(r"(\d{4})", candidate)
                if m:
                    year = int(m.group(1))
                    return f"{(year // 100) + 1}th"
    return ""


def parse_poem(path: str) -> List[BenchmarkLine]:
    """Parse a single 4B4V TEI XML file into benchmark lines."""
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return []

    root = tree.getroot()
    century = _extract_century(root)
    filename = os.path.basename(path)
    lines: List[BenchmarkLine] = []
    line_num = 0

    # Search for <l> elements in both namespaced and non-namespaced forms.
    for tag in ["l", f"{{{TEI_NS}}}l"]:
        for el in root.iter(tag):
            met = el.get("met", "").strip()
            real = el.get("real", "").strip()
            # Use "real" if available (actual stress), else fall back to "met" (template).
            stress_raw = real or met
            if not stress_raw:
                continue

            # Extract text content from <seg> children, excluding <note> annotations.
            # Syllable breaks are encoded two ways in 4B4V:
            #   1. An explicit <sb/> element inside a <seg>
            #   2. A segment boundary where the seg text ends mid-word (no trailing space)
            # In either case, do NOT insert whitespace between this seg and the next.
            _NOTE_TAGS = {f"{{{TEI_NS}}}note", "note"}
            parts: List[str] = []
            if el.text:
                parts.append(el.text)
            children = [c for c in el if c.tag not in _NOTE_TAGS]
            for child in children:
                seg_parts: List[str] = []
                for grandchild in child.iter():
                    if grandchild is child:
                        if grandchild.text:
                            seg_parts.append(grandchild.text)
                    elif grandchild.tag not in _NOTE_TAGS:
                        if grandchild.text:
                            seg_parts.append(grandchild.text)
                        if grandchild.tail:
                            seg_parts.append(grandchild.tail)
                seg_text = "".join(seg_parts)
                parts.append(seg_text)
                # Only include the inter-element whitespace (tail) if this segment's
                # text ends with a space — meaning the word is complete.
                if child.tail and seg_text.endswith((" ", "\n", "\t")):
                    parts.append(child.tail)
            text = "".join(parts).strip()
            text = re.sub(r"\s+", " ", text)
            if not text:
                continue

            gold_stress = _convert_stress(stress_raw)
            gold_template = _convert_stress(met) if met else gold_stress
            if not gold_stress:
                continue

            line_num += 1
            gold_meter = _infer_meter(gold_template)

            lines.append(BenchmarkLine(
                poem_file=filename,
                line_number=line_num,
                text=text,
                gold_stress=gold_stress,
                gold_template=gold_template,
                gold_meter=gold_meter,
                century=century,
            ))

    return lines


def parse_corpus(data_dir: str) -> List[BenchmarkLine]:
    """Parse all TEI XML files in the given directory tree."""
    all_lines: List[BenchmarkLine] = []
    if not os.path.isdir(data_dir):
        return all_lines

    for dirpath, _, filenames in os.walk(data_dir):
        for fname in sorted(filenames):
            if not fname.endswith(".xml"):
                continue
            path = os.path.join(dirpath, fname)
            all_lines.extend(parse_poem(path))

    return all_lines


def corpus_stats(lines: List[BenchmarkLine]) -> Dict[str, object]:
    """Compute summary statistics for a parsed corpus."""
    meters: Dict[str, int] = {}
    centuries: Dict[str, int] = {}
    poems: set = set()

    for line in lines:
        meters[line.gold_meter] = meters.get(line.gold_meter, 0) + 1
        if line.century:
            centuries[line.century] = centuries.get(line.century, 0) + 1
        poems.add(line.poem_file)

    return {
        "total_lines": len(lines),
        "total_poems": len(poems),
        "meters": dict(sorted(meters.items(), key=lambda kv: -kv[1])),
        "centuries": dict(sorted(centuries.items())),
    }


if __name__ == "__main__":
    import sys
    data_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "data", "poems")
    lines = parse_corpus(data_dir)
    if not lines:
        print(f"No annotated lines found in {data_dir}")
        print("See benchmarks/data/.gitignore for instructions on obtaining the 4B4V corpus.")
        sys.exit(1)
    stats = corpus_stats(lines)
    import json
    print(json.dumps(stats, indent=2))
