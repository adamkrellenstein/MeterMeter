import gzip
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    from .heuristics import estimate_stress_pattern
except ImportError:
    from heuristics import estimate_stress_pattern

TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

FOOT_TEMPLATES = {
    "iambic": "US",
    "trochaic": "SU",
    "anapestic": "UUS",
    "dactylic": "SUU",
}

LINE_NAME_BY_FEET = {
    1: "monometer",
    2: "dimeter",
    3: "trimeter",
    4: "tetrameter",
    5: "pentameter",
    6: "hexameter",
}


@dataclass
class LineAnalysis:
    line_no: int
    source_text: str
    tokens: List[str]
    stress_pattern: str
    meter_name: str
    feet_count: int
    confidence: float
    oov_tokens: List[str]
    debug_scores: Dict[str, float]
    analysis_hint: str = ""
    source: str = "engine"


class MeterEngine:
    def __init__(self, dict_path: Optional[str] = None, word_patterns: Optional[Dict[str, List[str]]] = None):
        self._dict_path = dict_path or os.path.join(os.path.dirname(__file__), "cmudict_min.json.gz")
        self._word_patterns = word_patterns

    @property
    def word_patterns(self) -> Dict[str, List[str]]:
        if self._word_patterns is None:
            self._word_patterns = self._load_word_patterns(self._dict_path)
        return self._word_patterns

    def _load_word_patterns(self, path: str) -> Dict[str, List[str]]:
        if not os.path.exists(path):
            return {}

        with gzip.open(path, "rt", encoding="utf-8") as fh:
            raw = json.load(fh)

        out: Dict[str, List[str]] = {}
        for word, patterns in raw.items():
            if not isinstance(patterns, list):
                continue
            clean_patterns = [p for p in patterns if isinstance(p, str) and p and set(p).issubset({"U", "S"})]
            if clean_patterns:
                out[word.lower()] = clean_patterns
        return out

    def tokenize(self, line: str) -> List[str]:
        return TOKEN_RE.findall(line)

    def _lookup_word_pattern(self, word: str) -> Tuple[str, bool]:
        key = word.lower()
        patterns = self.word_patterns.get(key)
        if patterns:
            return patterns[0], True

        if key.endswith("'s"):
            base = key[:-2]
            patterns = self.word_patterns.get(base)
            if patterns:
                return patterns[0], True

        return estimate_stress_pattern(word), False

    def _pattern_distance(self, a: str, b: str, foot_name: str) -> float:
        if not a and not b:
            return 0.0

        common = min(len(a), len(b))
        mismatch = 0.0

        for idx in range(common):
            if a[idx] == b[idx]:
                continue
            # Lightly discount first-position inversion for alternating meters.
            if idx == 0 and foot_name in {"iambic", "trochaic"}:
                mismatch += 0.5
            else:
                mismatch += 1.0

        mismatch += abs(len(a) - len(b)) * 0.85
        return mismatch

    def _meter_candidates(self, pattern: str) -> List[Tuple[str, int, float]]:
        if not pattern:
            return []

        candidates: List[Tuple[str, int, float]] = []
        syllables = len(pattern)

        for foot_name, template_unit in FOOT_TEMPLATES.items():
            unit = len(template_unit)
            approx_feet = max(1, int(round(syllables / float(unit))))
            feet_options = range(max(1, approx_feet - 1), min(6, approx_feet + 1) + 1)

            for feet in feet_options:
                template = template_unit * feet
                dist = self._pattern_distance(pattern, template, foot_name)
                normalizer = max(len(pattern), len(template), 1)
                score = max(0.0, 1.0 - (dist / normalizer))
                candidates.append((foot_name, feet, score))

        candidates.sort(key=lambda item: item[2], reverse=True)
        return candidates

    def analyze_line(self, line: str, line_no: int = 0) -> Optional[LineAnalysis]:
        if not line.strip():
            return None

        tokens = self.tokenize(line)
        if not tokens:
            return None

        patterns: List[str] = []
        oov_tokens: List[str] = []

        for token in tokens:
            pattern, found = self._lookup_word_pattern(token)
            if not pattern:
                continue
            patterns.append(pattern)
            if not found:
                oov_tokens.append(token.lower())

        if not patterns:
            return None

        stress_pattern = "".join(patterns)
        candidates = self._meter_candidates(stress_pattern)
        if not candidates:
            return None

        best_name, best_feet, best_score = candidates[0]
        second_score = candidates[1][2] if len(candidates) > 1 else 0.0
        margin = max(0.0, best_score - second_score)
        oov_ratio = len(oov_tokens) / float(len(tokens)) if tokens else 0.0

        confidence = (best_score * 0.72) + (margin * 0.2) + ((1.0 - oov_ratio) * 0.08)
        confidence = max(0.0, min(1.0, confidence))

        line_name = LINE_NAME_BY_FEET.get(best_feet, f"{best_feet}-foot")
        meter_name = f"{best_name} {line_name}"

        debug_scores = {
            f"{name}:{feet}": score for name, feet, score in candidates[:6]
        }

        return LineAnalysis(
            line_no=line_no,
            source_text=line,
            tokens=tokens,
            stress_pattern=stress_pattern,
            meter_name=meter_name,
            feet_count=best_feet,
            confidence=confidence,
            oov_tokens=oov_tokens,
            debug_scores=debug_scores,
        )

    def analyze_lines(self, lines: List[str], start_line_no: int = 0) -> Dict[int, LineAnalysis]:
        out: Dict[int, LineAnalysis] = {}
        for idx, line in enumerate(lines):
            analysis = self.analyze_line(line, line_no=start_line_no + idx)
            if analysis is not None:
                out[analysis.line_no] = analysis
        return out
