import gzip
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .heuristics import clean_word, estimate_stress_pattern, estimate_syllables

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

# Small built-in lexicon for common poetic/archaic forms and high-impact words.
# This supplements the tiny bundled CMU subset.
BUILTIN_WORD_PATTERNS: Dict[str, List[str]] = {
    "i": ["U"],
    "shall": ["U", "S"],
    "thou": ["U", "S"],
    "thee": ["U", "S"],
    "thy": ["U", "S"],
    "hath": ["U", "S"],
    "ow'st": ["S"],
    "wander'st": ["SU", "US"],
    "grow'st": ["S"],
    "temperate": ["SU", "SUU"],
    "compare": ["US"],
    "lovely": ["SU"],
    "darling": ["SU"],
    "summer": ["SU"],
    "sometime": ["SU"],
    "heaven": ["SU"],
    "marriage": ["SU"],
    "admit": ["US"],
    "impediments": ["USUU", "USU"],
    "wandering": ["SU", "SUU"],
    "even": ["U", "SU", "S"],
    "love's": ["S"],
    "time's": ["S"],
    "rosy": ["SU"],
    "music": ["SU"],
    "mistress": ["SU"],
    "wires": ["S", "SU"],
    "damasked": ["SU", "US"],
    "perfumes": ["US", "SU"],
    "delight": ["US"],
    "often": ["SU"],
    "every": ["SU"],
    "declines": ["US"],
    "nature's": ["SU"],
    "changing": ["SU"],
    "untrimmed": ["US"],
    "eternal": ["USU"],
    "possession": ["USU"],
    "complexion": ["USU"],
    "lives": ["S", "U"],
    "gives": ["S", "U"],
    "more": ["U", "S"],
    "too": ["U", "S"],
    "nor": ["U", "S"],
    "this": ["U", "S"],
    "that": ["U", "S"],
    "but": ["U", "S"],
    "let": ["U", "S"],
    "me": ["U", "S"],
    "it": ["U", "S"],
    "heaven": ["S", "SU"],
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
    token_patterns: List[str] = field(default_factory=list)
    analysis_hint: str = ""
    source: str = "engine"


class MeterEngine:
    def __init__(self, dict_path: Optional[str] = None, word_patterns: Optional[Dict[str, List[str]]] = None):
        if dict_path:
            self._dict_path = dict_path
        else:
            self._dict_path = os.path.join(os.path.dirname(__file__), "cmudict_min.json.gz")
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
        builtin = BUILTIN_WORD_PATTERNS.get(key)
        if builtin:
            return builtin[0], True

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

    def _lookup_word_patterns(self, word: str) -> Tuple[List[str], bool]:
        key = word.lower()
        builtin = BUILTIN_WORD_PATTERNS.get(key)
        if builtin:
            return list(builtin), True

        patterns = self.word_patterns.get(key)
        if patterns:
            return list(patterns), True

        if key.endswith("'s"):
            base = key[:-2]
            patterns = self.word_patterns.get(base)
            if patterns:
                return list(patterns), True

        return [], False

    def _is_function_word(self, word: str) -> bool:
        return clean_word(word) in {
            "a",
            "an",
            "and",
            "as",
            "at",
            "be",
            "but",
            "by",
            "for",
            "from",
            "if",
            "in",
            "into",
            "is",
            "it",
            "nor",
            "of",
            "on",
            "or",
            "so",
            "than",
            "that",
            "the",
            "their",
            "them",
            "then",
            "there",
            "these",
            "they",
            "this",
            "to",
            "up",
            "was",
            "we",
            "were",
            "what",
            "when",
            "where",
            "which",
            "who",
            "with",
            "you",
            "your",
            "thou",
            "thy",
            "thee",
            "shall",
            "do",
            "hath",
            "can",
            "not",
        }

    def _single_stress_pattern(self, syllables: int, stress_idx: int) -> str:
        if syllables <= 0:
            return ""
        stress_idx = max(0, min(stress_idx, syllables - 1))
        pats = ["U"] * syllables
        pats[stress_idx] = "S"
        return "".join(pats)

    def _token_pattern_options(self, token: str) -> Tuple[List[Tuple[str, float]], bool]:
        direct, found = self._lookup_word_patterns(token)
        options: List[Tuple[str, float]] = []
        seen = set()
        for pat in direct:
            if pat and pat not in seen:
                seen.add(pat)
                options.append((pat, 0.0))
        if found and options:
            return options, True

        clean = clean_word(token)
        syllables = estimate_syllables(clean or token)
        if syllables <= 0:
            return [("S", 0.0)], False

        base = estimate_stress_pattern(token)
        base_idx = max(0, (base.find("S") if "S" in base else 0))
        is_fn = self._is_function_word(token)

        if syllables == 1:
            # Keep monosyllables flexible; this is critical for iambic lines where many content
            # words are weak in context.
            if is_fn:
                return [("U", 0.0), ("S", 0.25)], False
            return [("S", 0.0), ("U", 0.25)], False

        for idx in range(syllables):
            pat = self._single_stress_pattern(syllables, idx)
            if pat in seen:
                continue
            seen.add(pat)
            penalty = 0.15 * abs(idx - base_idx)
            # Bias polysyllabic function words to weak openings / tail stress.
            if is_fn and idx < (syllables - 1):
                penalty += 0.1
            options.append((pat, penalty))

        if not options:
            options.append((base, 0.0))
        return options, False

    def _pattern_distance(self, a: str, b: str, foot_name: str) -> float:
        if not a and not b:
            return 0.0

        common = min(len(a), len(b))
        mismatch = 0.0

        for idx in range(common):
            if a[idx] == b[idx]:
                continue
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

    def _template_for_meter(self, foot_name: str, feet: int) -> str:
        unit = FOOT_TEMPLATES[foot_name]
        return unit * feet

    def _foot_position_penalty(self, pattern: str, foot_name: str, feet: int) -> float:
        if not pattern:
            return 0.0
        if foot_name not in {"iambic", "trochaic"}:
            return 0.0

        unit = FOOT_TEMPLATES[foot_name]
        out = 0.0
        n = min(len(pattern), len(unit) * feet)
        for i in range(n):
            expected = unit[i % 2]
            got = pattern[i]
            if got == expected:
                continue

            foot_idx = i // 2
            in_foot_pos = i % 2

            # Iambic pentameter prior: allow common substitutions without fully rewarding them.
            # - first-foot inversion (trochaic opening): cheap mismatch at positions 0/1
            # - occasional spondee in strong middle feet: moderate penalty
            if foot_name == "iambic":
                if foot_idx == 0:
                    out += 0.16
                    continue
                if in_foot_pos == 0:
                    # stress where weak expected (possible spondee/trochee substitution)
                    out += 0.34
                else:
                    # weak where strong expected
                    out += 0.58
                continue

            # Trochaic prior (mirror behavior, slightly weaker because our corpus target is mostly iambic).
            if foot_idx == 0:
                out += 0.18
            elif in_foot_pos == 0:
                out += 0.38
            else:
                out += 0.54

        return out

    def _best_fit_for_meter(self, tokens: List[str], foot_name: str, feet: int) -> Tuple[str, float, List[str], List[str]]:
        template = self._template_for_meter(foot_name, feet)
        templ_len = len(template)

        chosen_patterns: List[str] = []
        oov_tokens: List[str] = []
        built = []
        option_penalty = 0.0
        total_syllables = 0

        for tok in tokens:
            opts, found = self._token_pattern_options(tok)
            if not found:
                oov_tokens.append(tok.lower())
            best_pat = ""
            best_cost = None
            current_start = total_syllables
            for pat, pen in opts:
                # Compare this token chunk against corresponding meter window.
                chunk = template[current_start : current_start + len(pat)]
                mismatch = self._pattern_distance(pat, chunk, foot_name)
                cost = mismatch + pen
                if best_cost is None or cost < best_cost:
                    best_cost = cost
                    best_pat = pat
            if not best_pat:
                best_pat = opts[0][0]
                best_cost = 0.0
            chosen_patterns.append(best_pat)
            built.append(best_pat)
            option_penalty += float(best_cost or 0.0)
            total_syllables += len(best_pat)

        stress_pattern = "".join(built)
        dist = self._pattern_distance(stress_pattern, template, foot_name)
        # Include lexical choice penalty; normalize to pattern length.
        dist += option_penalty * 0.5
        dist += self._foot_position_penalty(stress_pattern, foot_name, feet)
        normalizer = max(len(stress_pattern), templ_len, 1)
        score = max(0.0, 1.0 - (dist / normalizer))

        # Prior: sonnet-like line lengths favor binary feet over ternary feet.
        if 9 <= len(stress_pattern) <= 11:
            if foot_name in {"iambic", "trochaic"} and feet == 5:
                if foot_name == "iambic":
                    score += 0.18
                else:
                    score += 0.03
            if foot_name in {"anapestic", "dactylic"}:
                score -= 0.14
        score = max(0.0, min(1.0, score))
        return stress_pattern, score, chosen_patterns, oov_tokens

    def analyze_line(self, line: str, line_no: int = 0) -> Optional[LineAnalysis]:
        if not line.strip():
            return None

        tokens = self.tokenize(line)
        if not tokens:
            return None

        # First pass: approximate pattern for rough candidate generation.
        approx_patterns: List[str] = []
        for token in tokens:
            pattern, _ = self._lookup_word_pattern(token)
            if pattern:
                approx_patterns.append(pattern)
        if not approx_patterns:
            return None

        approx_stress_pattern = "".join(approx_patterns)
        rough = self._meter_candidates(approx_stress_pattern)
        if not rough:
            return None

        # Refine top candidates by selecting token stress variants to best match each template.
        refined: List[Tuple[str, int, float, str, List[str], List[str]]] = []
        for foot_name, feet, _ in rough[:8]:
            sp, score, tok_pats, oov = self._best_fit_for_meter(tokens, foot_name, feet)
            refined.append((foot_name, feet, score, sp, tok_pats, oov))

        refined.sort(key=lambda item: item[2], reverse=True)
        best_name, best_feet, best_score, stress_pattern, best_token_patterns, oov_tokens = refined[0]
        second_score = refined[1][2] if len(refined) > 1 else 0.0
        margin = max(0.0, best_score - second_score)
        oov_ratio = len(oov_tokens) / float(len(tokens)) if tokens else 0.0

        confidence = (best_score * 0.72) + (margin * 0.2) + ((1.0 - oov_ratio) * 0.08)
        confidence = max(0.0, min(1.0, confidence))

        line_name = LINE_NAME_BY_FEET.get(best_feet, f"{best_feet}-foot")
        meter_name = f"{best_name} {line_name}"

        debug_scores = {f"{name}:{feet}": score for name, feet, score, _, _, _ in refined[:6]}

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
            token_patterns=list(best_token_patterns),
        )
