import gzip
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .heuristics import FUNCTION_WORDS, _build_pattern, clean_word, estimate_stress_pattern, estimate_syllables

TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

FOOT_TEMPLATES = {
    "iambic": "US",
    "trochaic": "SU",
    "anapestic": "UUS",
    "dactylic": "SUU",
}

METER_NAME_RE = re.compile(
    r"^\s*(iambic|trochaic|anapestic|dactylic)\s+(monometer|dimeter|trimeter|tetrameter|pentameter|hexameter)\s*$"
)

LINE_NAME_BY_FEET = {
    1: "monometer",
    2: "dimeter",
    3: "trimeter",
    4: "tetrameter",
    5: "pentameter",
    6: "hexameter",
}

# -- Pattern distance scoring --
# Reduced mismatch cost at first position of binary feet (allows trochaic opening etc.).
BINARY_FIRST_POS_DISCOUNT = 0.5
# Per-syllable penalty for length difference between pattern and template.
LENGTH_MISMATCH_COST = 0.85

# -- Foot position penalties for iambic meter --
IAMBIC_FIRST_FOOT_PENALTY = 0.16       # Trochaic opening is common; cheap.
IAMBIC_SPONDEE_PENALTY = 0.34          # Stress where weak expected (spondee/trochee sub).
IAMBIC_WEAK_STRONG_PENALTY = 0.58      # Weak where strong expected; costly.

# -- Foot position penalties for trochaic meter (mirror of iambic, weaker bias) --
TROCHAIC_FIRST_FOOT_PENALTY = 0.18
TROCHAIC_SPONDEE_PENALTY = 0.38
TROCHAIC_WEAK_STRONG_PENALTY = 0.54

# -- Pentameter prior for 9-11 syllable lines (sonnet assumption) --
IAMBIC_PENTAMETER_BONUS = 0.18
TROCHAIC_PENTAMETER_BONUS = 0.03
TERNARY_METER_PENALTY = 0.14
# Iambic pentameter wins if within this margin of the best candidate.
IAMBIC_BIAS_THRESHOLD = 0.10

# -- Token pattern options --
STRESS_INDEX_PENALTY_PER_POS = 0.15    # OOV: penalty per position from estimated stress.
FN_WORD_EARLY_STRESS_PENALTY = 0.1     # Polysyllabic function words: penalize early stress.

# -- Best-fit blending --
OPTION_PENALTY_BLEND = 0.5             # How much lexical choice penalty affects distance.
GLOBAL_FIT_WEIGHT = 0.75               # Weight of global meter fit in final score.
LEXICAL_FIT_WEIGHT = 0.25              # Weight of adjusted pattern score in final score.

# -- Confidence formula --
CONF_SCORE_WEIGHT = 0.72               # Best meter score.
CONF_MARGIN_WEIGHT = 0.20              # Margin over second-best.
CONF_OOV_WEIGHT = 0.08                 # Penalty for out-of-vocabulary tokens.

def _is_valid_pattern(p: object) -> bool:
    return isinstance(p, str) and bool(p) and set(p).issubset({"U", "S"})


def _load_builtin_lexicon() -> Dict[str, List[str]]:
    path = os.path.join(os.path.dirname(__file__), "builtin_lexicon.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out: Dict[str, List[str]] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, list):
            continue
        clean = [p for p in v if _is_valid_pattern(p)]
        if clean:
            out[k.lower()] = clean
    return out


BUILTIN_WORD_PATTERNS: Dict[str, List[str]] = _load_builtin_lexicon()


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
            clean_patterns = [p for p in patterns if _is_valid_pattern(p)]
            if clean_patterns:
                out[word.lower()] = clean_patterns
        return out

    def tokenize(self, line: str) -> List[str]:
        return TOKEN_RE.findall(line)

    def _resolve_word_entries(self, word: str) -> Tuple[List[str], bool]:
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
        return clean_word(word) in FUNCTION_WORDS

    def _token_pattern_options(self, token: str) -> Tuple[List[Tuple[str, float]], bool]:
        direct, found = self._resolve_word_entries(token)
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
            pat = _build_pattern(syllables, idx)
            if pat in seen:
                continue
            seen.add(pat)
            penalty = STRESS_INDEX_PENALTY_PER_POS * abs(idx - base_idx)
            # Bias polysyllabic function words to weak openings / tail stress.
            if is_fn and idx < (syllables - 1):
                penalty += FN_WORD_EARLY_STRESS_PENALTY
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
                mismatch += BINARY_FIRST_POS_DISCOUNT
            else:
                mismatch += 1.0

        mismatch += abs(len(a) - len(b)) * LENGTH_MISMATCH_COST
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

    def _score_pattern_for_meter(self, pattern: str, foot_name: str, feet: int) -> float:
        if not pattern:
            return 0.0
        template = self._template_for_meter(foot_name, feet)
        dist = self._pattern_distance(pattern, template, foot_name)
        dist += self._foot_position_penalty(pattern, foot_name, feet)
        normalizer = max(len(pattern), len(template), 1)
        score = max(0.0, 1.0 - (dist / normalizer))
        # Prior: sonnet-like line lengths favor binary feet over ternary feet.
        if 9 <= len(pattern) <= 11:
            if foot_name in {"iambic", "trochaic"} and feet == 5:
                if foot_name == "iambic":
                    score += IAMBIC_PENTAMETER_BONUS
                else:
                    score += TROCHAIC_PENTAMETER_BONUS
            if foot_name in {"anapestic", "dactylic"}:
                score -= TERNARY_METER_PENALTY
        return max(0.0, min(1.0, score))

    def _parse_meter_name(self, meter_name: str) -> Optional[Tuple[str, int]]:
        m = METER_NAME_RE.match((meter_name or "").strip().lower())
        if not m:
            return None
        foot_name, feet_name = m.group(1), m.group(2)
        feet = None
        for n, label in LINE_NAME_BY_FEET.items():
            if label == feet_name:
                feet = n
                break
        if feet is None:
            return None
        return foot_name, feet

    def score_stress_pattern_for_meter(self, stress_pattern: str, meter_name: str) -> Optional[float]:
        parsed = self._parse_meter_name(meter_name)
        if not parsed:
            return None
        foot_name, feet = parsed
        pattern = "".join(ch for ch in (stress_pattern or "").upper() if ch in {"U", "S"})
        if not pattern:
            return None
        return self._score_pattern_for_meter(pattern, foot_name, feet)

    def best_meter_for_stress_pattern(self, stress_pattern: str) -> Tuple[str, float, Dict[str, float]]:
        pattern = "".join(ch for ch in (stress_pattern or "").upper() if ch in {"U", "S"})
        if not pattern:
            return "", 0.0, {"margin": 0.0}

        candidates: List[Tuple[str, int, float]] = self._meter_candidates(pattern)
        rescored: List[Tuple[str, int, float]] = []
        for foot_name, feet, _ in candidates:
            score = self._score_pattern_for_meter(pattern, foot_name, feet)
            rescored.append((foot_name, feet, score))

        if not rescored:
            return "", 0.0, {"margin": 0.0}
        rescored.sort(key=lambda item: item[2], reverse=True)
        best_name, best_feet, best_score = rescored[0]
        iambic_bias = False
        if 9 <= len(pattern) <= 11:
            iambic_score = self._score_pattern_for_meter(pattern, "iambic", 5)
            if (
                iambic_score is not None
                and (best_name != "iambic" or best_feet != 5)
                and iambic_score >= (best_score - IAMBIC_BIAS_THRESHOLD)
            ):
                best_name, best_feet, best_score = "iambic", 5, iambic_score
                iambic_bias = True
        second_score = 0.0
        for name, feet, score in rescored:
            if name == best_name and feet == best_feet:
                continue
            if score > second_score:
                second_score = score
        margin = max(0.0, best_score - second_score)
        line_name = LINE_NAME_BY_FEET.get(best_feet, f"{best_feet}-foot")
        meter_name = f"{best_name} {line_name}"
        debug = {
            "margin": margin,
            "second_score": second_score,
            "iambic_bias": iambic_bias,
        }
        for i, (name, feet, score) in enumerate(rescored[:4], start=1):
            debug[f"top{i}_{name}_{feet}"] = score
        return meter_name, best_score, debug

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
                    out += IAMBIC_FIRST_FOOT_PENALTY
                    continue
                if in_foot_pos == 0:
                    # stress where weak expected (possible spondee/trochee substitution)
                    out += IAMBIC_SPONDEE_PENALTY
                else:
                    # weak where strong expected
                    out += IAMBIC_WEAK_STRONG_PENALTY
                continue

            # Trochaic prior (mirror behavior, slightly weaker because our corpus target is mostly iambic).
            if foot_idx == 0:
                out += TROCHAIC_FIRST_FOOT_PENALTY
            elif in_foot_pos == 0:
                out += TROCHAIC_SPONDEE_PENALTY
            else:
                out += TROCHAIC_WEAK_STRONG_PENALTY

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
        # Include lexical choice penalty before final meter scoring.
        dist += option_penalty * OPTION_PENALTY_BLEND
        normalizer = max(len(stress_pattern), templ_len, 1)
        adjusted_pattern_score = max(0.0, 1.0 - (dist / normalizer))
        score = self._score_pattern_for_meter(stress_pattern, foot_name, feet)
        # Blend lexical fit and global meter fit.
        score = max(0.0, min(1.0, (score * GLOBAL_FIT_WEIGHT) + (adjusted_pattern_score * LEXICAL_FIT_WEIGHT)))
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
            entries, found = self._resolve_word_entries(token)
            pattern = entries[0] if entries else (estimate_stress_pattern(token) if not found else "")
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
        syllable_len = len(stress_pattern)
        iambic_bias = False
        if 9 <= syllable_len <= 11:
            for name, feet, score, sp, tok_pats, oov in refined:
                if name == "iambic" and feet == 5:
                    if score >= (best_score - IAMBIC_BIAS_THRESHOLD) and (name != best_name or feet != best_feet):
                        best_name, best_feet, best_score = name, feet, score
                        stress_pattern = sp
                        best_token_patterns = tok_pats
                        oov_tokens = oov
                        iambic_bias = True
                    break
        second_score = 0.0
        for name, feet, score, _, _, _ in refined:
            if name == best_name and feet == best_feet:
                continue
            if score > second_score:
                second_score = score
        margin = max(0.0, best_score - second_score)
        oov_ratio = len(oov_tokens) / float(len(tokens)) if tokens else 0.0

        confidence = (best_score * CONF_SCORE_WEIGHT) + (margin * CONF_MARGIN_WEIGHT) + ((1.0 - oov_ratio) * CONF_OOV_WEIGHT)
        confidence = max(0.0, min(1.0, confidence))

        line_name = LINE_NAME_BY_FEET.get(best_feet, f"{best_feet}-foot")
        meter_name = f"{best_name} {line_name}"

        debug_scores = {f"{name}:{feet}": score for name, feet, score, _, _, _ in refined[:6]}
        if iambic_bias:
            debug_scores["bias:iambic"] = best_score

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
