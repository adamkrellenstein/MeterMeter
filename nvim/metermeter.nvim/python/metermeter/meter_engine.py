import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import prosodic

# Match Unicode letter tokens, allowing a single internal apostrophe (straight or curly).
# - `\w` includes letters, digits, and underscore in Unicode mode.
# - `[^\W\d_]` narrows that to letters (exclude non-word, digits, underscore).
TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")

# Monosyllabic function words: default to unstressed in metrical context.
# Based on Groves' rules (used by ZeuScansion) and the Scandroid's dictionary.
UNSTRESSED_MONOSYLLABLES = frozenset({
    # Articles
    "a", "an", "the",
    # Prepositions
    "at", "by", "for", "from", "in", "of", "on", "per", "to", "with",
    # Conjunctions
    "and", "but", "or", "nor", "if", "as", "than", "so", "yet",
    # Contractions / particles that prosodic marks stressed but behave as clitics
    "twas", "tis",
    # Verbs that prosodic marks stressed but are function-word-like in verse
    "let",
    # Auxiliary / modal verbs
    "am", "are", "be", "been", "can", "could", "did", "do", "does",
    "had", "has", "have", "is", "may", "might", "must", "shall",
    "should", "was", "were", "will", "would",
    # Pronouns
    "i", "me", "my", "he", "him", "his", "she", "her", "it", "its",
    "we", "us", "our", "they", "them", "their", "you", "your",
    "who", "whom", "whose", "which",
    # Determiners
    "some", "this", "these", "those",
})

# Monosyllables predominantly stressed in verse (corpus analysis of 4B4V).
# These are either wrongly marked unstressed by prosodic/CMU Dict, or were
# incorrectly included in UNSTRESSED_MONOSYLLABLES.
STRESSED_MONOSYLLABLES = frozenset({
    # These words are predominantly stressed in the 4B4V corpus but
    # are marked unstressed by prosodic/CMU Dict, or were in UNSTRESSED_MONOSYLLABLES.
    "all",   # 96% S
    "round",  # 100% S
    "here",  # 100% S
    "own",   # 100% S
    "each",  # 89% S
    "there",  # 83% S
    "off",   # 80% S
    "down",  # 92% S
    "up",    # 77% S
    "such",  # 73% S
    "out",   # 76% S
    "more",  # 75% S
    "one",   # 79% S
    "what",  # 56% S
    "not",   # 55% S
    "then",  # 61% S (discourse connector, often in strong position)
    "art",   # 64% S (archaic 2nd-person "thou art")
})

FOOT_TEMPLATES = {
    "iambic": "US",
    "trochaic": "SU",
    "anapestic": "UUS",
    "dactylic": "SUU",
}

# Allowed syllable-count deltas relative to the strict template length
# (len(FOOT_TEMPLATES[foot]) * feet). This makes meter classification stricter:
# we only consider a meter when the line's syllable count matches one of the
# explicit, meter-specific lengths.
#
# The allowed deviations encode common extrametrical patterns:
# - iambic: allow +1 for a feminine ending (extra trailing "U")
# - trochaic/dactylic: allow -1 for catalexis (missing final slot)
# - anapestic: allow -1 for an opening iamb substitution (drop leading "U")
METER_LENGTH_DELTAS: Dict[str, Tuple[int, ...]] = {
    "iambic": (0, 1),
    "trochaic": (-1, 0),
    "anapestic": (-1, 0),
    "dactylic": (-1, 0),
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

# Pattern scoring constants for deterministic API compatibility.
BINARY_FIRST_POS_DISCOUNT = 0.5
LENGTH_MISMATCH_COST = 0.85
FEMININE_ENDING_COST = 0.30
IAMBIC_PENTAMETER_BONUS = 0.18
TROCHAIC_PENTAMETER_BONUS = 0.03
TERNARY_METER_PENALTY = 0.14
IAMBIC_BIAS_THRESHOLD = 0.10
IAMBIC_HEXAMETER_BONUS = 0.12
IAMBIC_FIRST_FOOT_PENALTY = 0.16
IAMBIC_SPONDEE_PENALTY = 0.34
IAMBIC_WEAK_STRONG_PENALTY = 0.58
TROCHAIC_FIRST_FOOT_PENALTY = 0.18
TROCHAIC_SPONDEE_PENALTY = 0.38
TROCHAIC_WEAK_STRONG_PENALTY = 0.54

# Ambiguity / context priors.
MONO_FLIP_COST = 1.30
POLY_FLIP_COST = 1.75
FUNCTION_TO_STRONG_FLIP_COST = 0.85
STRONG_TO_WEAK_FLIP_COST = 1.05
CONTEXT_PRIOR_MAX_BONUS = 0.12


@dataclass
class LineAnalysis:
    line_no: int
    source_text: str
    tokens: List[str]
    stress_pattern: str
    meter_name: str
    feet_count: int
    confidence: float
    debug_scores: Dict[str, float]
    token_patterns: List[str] = field(default_factory=list)
    # Flat per-syllable (text, is_strong) pairs from prosodic, used for highlighting.
    syllable_positions: List[Tuple[str, bool]] = field(default_factory=list)
    # Flat per-syllable char spans in source_text.
    syllable_char_spans: List[Tuple[int, int]] = field(default_factory=list)


@dataclass
class _SyllableUnit:
    text: str
    token_index: int
    char_start: int
    char_end: int
    options: Tuple[Tuple[str, float], ...]
    default_stress: str


@dataclass
class _MeterPath:
    foot_name: str
    feet: int
    base_score: float
    adjusted_score: float
    cost: float
    pattern: str
    context_bonus: float


class MeterEngine:
    def __init__(self) -> None:
        pass

    def tokenize(self, line: str) -> List[str]:
        return TOKEN_RE.findall(line)

    def meter_features_for(self, meter_name: str, stress_pattern: str) -> Dict[str, Any]:
        """Compute lightweight scansion notes from (meter_name, stress_pattern).

        This is intentionally simple (binary meters only) and is used to power
        Neovim end-of-line hint annotations like feminine endings and inversions.
        """
        out: Dict[str, Any] = {
            "ending": "unknown",  # "masc" | "fem" | "unknown"
            "inversion": False,
            "initial_inversion": False,
            "spondee": False,
            "pyrrhic": False,
        }

        parsed = self._parse_meter_name(meter_name)
        if parsed is None:
            return out
        foot_name, feet = parsed
        if foot_name not in {"iambic", "trochaic"}:
            return out

        if not isinstance(stress_pattern, str) or not stress_pattern:
            return out

        expected_len = len(FOOT_TEMPLATES[foot_name]) * int(feet)
        base = stress_pattern
        if len(stress_pattern) == expected_len + 1 and stress_pattern.endswith("U"):
            out["ending"] = "fem"
            base = stress_pattern[:-1]
        elif len(stress_pattern) == expected_len:
            out["ending"] = "masc"
        else:
            return out

        if len(base) != expected_len:
            return out

        expected_foot = FOOT_TEMPLATES[foot_name]
        inverted_foot = expected_foot[::-1]

        for foot_idx in range(int(feet)):
            seg = base[2 * foot_idx : 2 * foot_idx + 2]
            if seg == inverted_foot:
                out["inversion"] = True
                if foot_idx == 0:
                    out["initial_inversion"] = True
            elif seg == "SS":
                out["spondee"] = True
            elif seg == "UU":
                out["pyrrhic"] = True

        return out

    # -- Deterministic scoring helpers (API compatibility) --

    def _parse_meter_name(self, meter_name: str) -> Optional[Tuple[str, int]]:
        m = METER_NAME_RE.match((meter_name or "").strip().lower())
        if not m:
            return None
        foot_name, feet_name = m.group(1), m.group(2)
        for n, label in LINE_NAME_BY_FEET.items():
            if label == feet_name:
                return foot_name, n
        return None

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
        len_diff = abs(len(a) - len(b))
        if len_diff == 1 and len(a) > len(b) and a[-1] == "U":
            mismatch += FEMININE_ENDING_COST
        elif len_diff > 0:
            mismatch += len_diff * LENGTH_MISMATCH_COST
        return mismatch

    def _template_for_meter(self, foot_name: str, feet: int) -> str:
        return FOOT_TEMPLATES[foot_name] * feet

    def _allowed_syllable_counts_for_meter(self, foot_name: str, feet: int) -> Tuple[int, ...]:
        base_len = len(FOOT_TEMPLATES[foot_name]) * int(feet)
        deltas = METER_LENGTH_DELTAS.get(foot_name, (0,))
        allowed = sorted({base_len + delta for delta in deltas if (base_len + delta) > 0})
        return tuple(allowed)

    def _foot_position_penalty(self, pattern: str, foot_name: str, feet: int) -> float:
        if not pattern or foot_name not in {"iambic", "trochaic"}:
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
            if foot_name == "iambic":
                if foot_idx == 0:
                    out += IAMBIC_FIRST_FOOT_PENALTY
                elif in_foot_pos == 0:
                    out += IAMBIC_SPONDEE_PENALTY
                else:
                    out += IAMBIC_WEAK_STRONG_PENALTY
            else:
                if foot_idx == 0:
                    out += TROCHAIC_FIRST_FOOT_PENALTY
                elif in_foot_pos == 0:
                    out += TROCHAIC_SPONDEE_PENALTY
                else:
                    out += TROCHAIC_WEAK_STRONG_PENALTY
        return out

    def _score_pattern_for_meter(self, pattern: str, foot_name: str, feet: int) -> float:
        if not pattern:
            return 0.0
        template = self._template_for_meter(foot_name, feet)
        dist = self._pattern_distance(pattern, template, foot_name)
        dist += self._foot_position_penalty(pattern, foot_name, feet)
        normalizer = max(len(pattern), len(template), 1)
        score = max(0.0, 1.0 - (dist / normalizer))
        score = self._apply_meter_length_priors(score, foot_name, feet, len(pattern))
        return max(0.0, min(1.0, score))

    def _apply_meter_length_priors(self, score: float, foot_name: str, feet: int, pattern_len: int) -> float:
        out = score
        if 9 <= pattern_len <= 11:
            if foot_name == "trochaic" and feet == 5:
                out += TROCHAIC_PENTAMETER_BONUS
            elif foot_name in {"anapestic", "dactylic"}:
                out -= TERNARY_METER_PENALTY
            if foot_name == "iambic" and feet == 5 and pattern_len >= 10:
                out += IAMBIC_PENTAMETER_BONUS
        if 12 <= pattern_len <= 13:
            if foot_name == "iambic" and feet == 6:
                out += IAMBIC_HEXAMETER_BONUS
            elif foot_name in {"anapestic", "dactylic"}:
                out -= TERNARY_METER_PENALTY
        return max(0.0, min(1.0, out))

    def _meter_candidates(self, pattern: str) -> List[Tuple[str, int, float]]:
        if not pattern:
            return []
        candidates: List[Tuple[str, int, float]] = []
        syllables = len(pattern)
        for foot_name, template_unit in FOOT_TEMPLATES.items():
            for feet in range(1, 6 + 1):
                if syllables not in self._allowed_syllable_counts_for_meter(foot_name, feet):
                    continue
                template = template_unit * feet
                dist = self._pattern_distance(pattern, template, foot_name)
                normalizer = max(len(pattern), len(template), 1)
                score = max(0.0, 1.0 - (dist / normalizer))
                candidates.append((foot_name, feet, score))
        candidates.sort(key=lambda item: item[2], reverse=True)
        return candidates

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
        candidates = self._meter_candidates(pattern)
        rescored: List[Tuple[str, int, float]] = []
        for foot_name, feet, _ in candidates:
            score = self._score_pattern_for_meter(pattern, foot_name, feet)
            rescored.append((foot_name, feet, score))
        if not rescored:
            return "", 0.0, {"margin": 0.0}
        rescored.sort(key=lambda item: item[2], reverse=True)
        best_name, best_feet, best_score = rescored[0]
        iambic_bias = False
        iambic_bias_target = None
        if 10 <= len(pattern) <= 11:
            iambic_bias_target = 5
        elif 12 <= len(pattern) <= 13:
            iambic_bias_target = 6
        if iambic_bias_target is not None:
            iambic_score = self._score_pattern_for_meter(pattern, "iambic", iambic_bias_target)
            if (
                iambic_score is not None
                and (best_name != "iambic" or best_feet != iambic_bias_target)
                and iambic_score >= (best_score - IAMBIC_BIAS_THRESHOLD)
            ):
                best_name, best_feet, best_score = "iambic", iambic_bias_target, iambic_score
                iambic_bias = True
        second_score = 0.0
        for name, feet, score in rescored:
            if name == best_name and feet == best_feet:
                continue
            if score > second_score:
                second_score = score
        margin = max(0.0, best_score - second_score)
        line_name = LINE_NAME_BY_FEET.get(best_feet, f"{best_feet}-foot")
        meter_name_out = f"{best_name} {line_name}"
        debug: Dict[str, float] = {"margin": margin, "second_score": second_score, "iambic_bias": float(iambic_bias)}
        for i, (name, feet, score) in enumerate(rescored[:4], start=1):
            debug[f"top{i}_{name}_{feet}"] = score
        return meter_name_out, best_score, debug

    # -- Viterbi disambiguation helpers --

    def _coerce_context(self, context: Optional[Dict[str, Any]]) -> Tuple[str, float]:
        if not isinstance(context, dict):
            return "", 0.0
        meter = str(context.get("dominant_meter") or "").strip().lower()
        strength = context.get("dominant_strength")
        if not isinstance(strength, (int, float)):
            strength = 0.0
        strength_f = max(0.0, min(1.0, float(strength)))
        if meter == "":
            return "", 0.0
        if self._parse_meter_name(meter) is None:
            return "", 0.0
        return meter, strength_f

    def _options_for_syllable(self, word_text: str, is_monosyllable: bool, lexical_stressed: bool) -> Tuple[Tuple[str, float], ...]:
        if is_monosyllable and word_text in UNSTRESSED_MONOSYLLABLES:
            return (("U", 0.0), ("S", FUNCTION_TO_STRONG_FLIP_COST))
        if is_monosyllable and word_text in STRESSED_MONOSYLLABLES:
            return (("S", 0.0), ("U", STRONG_TO_WEAK_FLIP_COST))
        if lexical_stressed:
            flip = MONO_FLIP_COST if is_monosyllable else POLY_FLIP_COST
            return (("S", 0.0), ("U", flip))
        flip = MONO_FLIP_COST if is_monosyllable else POLY_FLIP_COST
        return (("U", 0.0), ("S", flip))

    def _position_mismatch_extra(self, foot_name: str, template_idx: int) -> float:
        if foot_name not in {"iambic", "trochaic"}:
            return 0.0
        foot_idx = template_idx // 2
        in_foot_pos = template_idx % 2
        if foot_name == "iambic":
            if foot_idx == 0:
                return IAMBIC_FIRST_FOOT_PENALTY
            if in_foot_pos == 0:
                return IAMBIC_SPONDEE_PENALTY
            return IAMBIC_WEAK_STRONG_PENALTY
        if foot_idx == 0:
            return TROCHAIC_FIRST_FOOT_PENALTY
        if in_foot_pos == 0:
            return TROCHAIC_SPONDEE_PENALTY
        return TROCHAIC_WEAK_STRONG_PENALTY

    def _mismatch_cost_at(self, foot_name: str, template_idx: int) -> float:
        base = 1.0
        if template_idx == 0 and foot_name in {"iambic", "trochaic"}:
            base = BINARY_FIRST_POS_DISCOUNT
        return base + self._position_mismatch_extra(foot_name, template_idx)

    def _candidate_meters_for_syllables(self, syllable_count: int) -> List[Tuple[str, int]]:
        out: List[Tuple[str, int]] = []
        for foot_name in FOOT_TEMPLATES.keys():
            for feet in range(1, 6 + 1):
                if syllable_count not in self._allowed_syllable_counts_for_meter(foot_name, feet):
                    continue
                out.append((foot_name, feet))
        return out

    def _viterbi_for_meter(self, syllables: List[_SyllableUnit], foot_name: str, feet: int) -> Tuple[str, float]:
        template = self._template_for_meter(foot_name, feet)
        n = len(syllables)
        m = len(template)
        len_diff = n - m
        inf = 1e12

        if len_diff not in {-1, 0, 1}:
            fallback = "".join(unit.default_stress for unit in syllables)
            return fallback, float("inf")

        dp: List[List[float]] = [[inf] * (m + 1) for _ in range(n + 1)]
        prev: List[List[Optional[Tuple[int, int, str, str]]]] = [[None] * (m + 1) for _ in range(n + 1)]
        dp[0][0] = 0.0

        allow_insert = len_diff < 0
        allow_delete = len_diff > 0

        for i in range(n + 1):
            for j in range(m + 1):
                cur = dp[i][j]
                if cur >= inf:
                    continue

                if i < n and j < m:
                    unit = syllables[i]
                    expected = template[j]
                    for stress, opt_cost in unit.options:
                        mismatch_cost = 0.0 if stress == expected else self._mismatch_cost_at(foot_name, j)
                        new_cost = cur + opt_cost + mismatch_cost
                        if new_cost < dp[i + 1][j + 1]:
                            dp[i + 1][j + 1] = new_cost
                            prev[i + 1][j + 1] = (i, j, "M", stress)

                if allow_delete and foot_name == "iambic" and i < n and j == m:
                    unit = syllables[i]
                    option_costs = dict(unit.options)
                    delete_stress = "U"
                    delete_opt_cost = float(option_costs.get("U", 0.0))
                    delete_penalty = LENGTH_MISMATCH_COST
                    if j == m and i == n - 1 and delete_stress == "U":
                        delete_penalty = FEMININE_ENDING_COST
                    new_cost = cur + delete_opt_cost + delete_penalty
                    if new_cost < dp[i + 1][j]:
                        dp[i + 1][j] = new_cost
                        prev[i + 1][j] = (i, j, "D", delete_stress)

                if allow_insert and j < m:
                    if foot_name == "anapestic":
                        if not (i == 0 and j == 0):
                            continue
                    else:
                        if i != n:
                            continue
                    new_cost = cur + LENGTH_MISMATCH_COST
                    if new_cost < dp[i][j + 1]:
                        dp[i][j + 1] = new_cost
                        prev[i][j + 1] = (i, j, "I", "")

        final_cost = dp[n][m]
        if final_cost >= inf:
            fallback = "".join(unit.default_stress for unit in syllables)
            return fallback, float("inf")

        pattern_rev: List[str] = []
        i = n
        j = m
        while i > 0 or j > 0:
            state = prev[i][j]
            if state is None:
                break
            pi, pj, action, stress = state
            if action in {"M", "D"}:
                pattern_rev.append(stress)
            i, j = pi, pj
        pattern = "".join(reversed(pattern_rev))
        if len(pattern) != n:
            pattern = "".join(unit.default_stress for unit in syllables)

        return pattern, final_cost

    def _best_meter_for_ambiguous_syllables(
        self,
        syllables: List[_SyllableUnit],
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, float, Dict[str, float], str]:
        if not syllables:
            return "", 0.0, {"margin": 0.0}, ""

        context_meter, context_strength = self._coerce_context(context)
        context_bonus_max = CONTEXT_PRIOR_MAX_BONUS * context_strength
        n_syllables = len(syllables)
        lexical_pattern = "".join(unit.default_stress for unit in syllables)
        meter_paths: List[_MeterPath] = []

        for foot_name, feet in self._candidate_meters_for_syllables(n_syllables):
            pattern, cost = self._viterbi_for_meter(syllables, foot_name, feet)
            if not pattern:
                continue
            template = self._template_for_meter(foot_name, feet)
            normalizer = max(len(pattern), len(template), 1)
            viterbi_score = max(0.0, 1.0 - (cost / normalizer))
            viterbi_score = self._apply_meter_length_priors(viterbi_score, foot_name, feet, len(pattern))
            lexical_score = self._score_pattern_for_meter(lexical_pattern, foot_name, feet)
            base_score = (0.50 * viterbi_score) + (0.50 * lexical_score)
            line_name = LINE_NAME_BY_FEET.get(feet, f"{feet}-foot")
            meter_name = f"{foot_name} {line_name}"
            context_bonus = context_bonus_max if meter_name == context_meter else 0.0
            adjusted_score = max(0.0, min(1.0, base_score + context_bonus))
            meter_paths.append(_MeterPath(
                foot_name=foot_name,
                feet=feet,
                base_score=base_score,
                adjusted_score=adjusted_score,
                cost=cost,
                pattern=pattern,
                context_bonus=context_bonus,
            ))

        if not meter_paths:
            fallback = "".join(unit.default_stress for unit in syllables)
            return "", 0.0, {"margin": 0.0}, fallback

        meter_paths.sort(key=lambda item: item.adjusted_score, reverse=True)
        best = meter_paths[0]

        iambic_bias = False
        iambic_bias_target = None
        if 10 <= n_syllables <= 11:
            iambic_bias_target = 5
        elif 12 <= n_syllables <= 13:
            iambic_bias_target = 6
        if iambic_bias_target is not None:
            for candidate in meter_paths:
                if candidate.foot_name == "iambic" and candidate.feet == iambic_bias_target:
                    if best != candidate and candidate.adjusted_score >= (best.adjusted_score - IAMBIC_BIAS_THRESHOLD):
                        best = candidate
                        iambic_bias = True
                    break

        second_score = 0.0
        for candidate in meter_paths:
            if candidate.foot_name == best.foot_name and candidate.feet == best.feet:
                continue
            if candidate.adjusted_score > second_score:
                second_score = candidate.adjusted_score

        margin = max(0.0, best.adjusted_score - second_score)
        line_name = LINE_NAME_BY_FEET.get(best.feet, f"{best.feet}-foot")
        meter_name_out = f"{best.foot_name} {line_name}"
        debug: Dict[str, float] = {
            "margin": margin,
            "second_score": second_score,
            "iambic_bias": float(iambic_bias),
            "context_strength": context_strength,
            "context_bonus": best.context_bonus,
        }
        for idx, candidate in enumerate(meter_paths[:4], start=1):
            debug[f"top{idx}_{candidate.foot_name}_{candidate.feet}"] = candidate.adjusted_score
            debug[f"top{idx}_{candidate.foot_name}_{candidate.feet}_base"] = candidate.base_score
        return meter_name_out, best.adjusted_score, debug, best.pattern

    def _align_syllables_in_token(
        self,
        line: str,
        token_start: int,
        token_end: int,
        syllable_texts: List[str],
    ) -> List[Tuple[int, int]]:
        token = line[token_start:token_end]
        token_lower = token.lower()
        local_cursor = 0
        out: List[Tuple[int, int]] = []
        exact = True

        for raw_text in syllable_texts:
            s = (raw_text or "").lower()
            if not s:
                exact = False
                out.append((token_start + local_cursor, token_start + local_cursor))
                continue
            idx = token_lower.find(s, local_cursor)
            if idx == -1:
                exact = False
                out.append((token_start + local_cursor, token_start + local_cursor))
                continue
            start = token_start + idx
            end = token_start + idx + len(s)
            out.append((start, end))
            local_cursor = idx + len(s)

        if exact:
            return out

        token_len = max(1, token_end - token_start)
        widths = [max(1, len((text or "").strip())) for text in syllable_texts]
        total_width = max(1, sum(widths))
        rebuilt: List[Tuple[int, int]] = []
        accum = 0
        for idx, width in enumerate(widths):
            start = token_start + int(round((accum / float(total_width)) * token_len))
            accum += width
            end = token_start + int(round((accum / float(total_width)) * token_len))
            if idx == len(widths) - 1:
                end = token_end
            if end <= start:
                end = min(token_end, start + 1)
            rebuilt.append((start, end))
        return rebuilt

    def analyze_line(
        self,
        line: str,
        line_no: int = 0,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[LineAnalysis]:
        if not line.strip():
            return None

        token_matches = list(TOKEN_RE.finditer(line))
        tokens = [m.group(0) for m in token_matches]
        token_spans = [(m.start(), m.end()) for m in token_matches]
        if not tokens:
            return None

        try:
            ptext = prosodic.Text(line)
            if not ptext.lines:
                return None
            pline = ptext.lines[0]
        except Exception:
            return None

        syllables: List[_SyllableUnit] = []
        token_patterns: List[str] = []
        token_cursor = 0
        line_char_len = len(line)

        for wt in pline.wordtokens:
            wtype = wt.wordtype
            if getattr(wtype, "is_punc", False):
                continue
            wf = wtype.form  # first (least-stressed) pronunciation variant
            syls = getattr(wf, "syllables", None)
            if not syls:
                continue

            token_index = min(token_cursor, max(0, len(tokens) - 1))
            if token_cursor < len(token_spans):
                token_start, token_end = token_spans[token_cursor]
                token_text = line[token_start:token_end]
                token_cursor += 1
            else:
                raw_word = str(getattr(wt, "txt", "") or "")
                raw_word_l = raw_word.lower().strip()
                # Search forward from the last token's end to avoid matching an
                # earlier occurrence of the same word (e.g. "love love love").
                search_from = token_spans[token_cursor - 1][1] if token_cursor > 0 and token_cursor <= len(token_spans) else 0
                found_pos = line.lower().find(raw_word_l, search_from)
                if found_pos == -1:
                    found_pos = max(0, search_from)
                token_start = max(0, min(line_char_len, found_pos))
                token_end = max(token_start + 1, min(line_char_len, token_start + max(1, len(raw_word_l))))
                token_text = line[token_start:token_end]

            word_text = token_text.strip().lower().replace("’", "'").strip("'")
            is_mono = len(syls) == 1
            syllable_texts = [str(getattr(syl, "txt", "") or "").lower() for syl in syls]
            syllable_spans = self._align_syllables_in_token(line, token_start, token_end, syllable_texts)

            token_pattern_default = ""
            for syl, syl_text, (span_start, span_end) in zip(syls, syllable_texts, syllable_spans):
                lexical_stressed = bool(getattr(syl, "is_stressed", False))
                options = self._options_for_syllable(word_text, is_mono, lexical_stressed)
                default_stress = min(options, key=lambda option: (option[1], option[0]))[0]
                token_pattern_default += default_stress
                syllables.append(_SyllableUnit(
                    text=syl_text,
                    token_index=token_index,
                    char_start=max(0, min(line_char_len, span_start)),
                    char_end=max(0, min(line_char_len, span_end)),
                    options=options,
                    default_stress=default_stress,
                ))
            token_patterns.append(token_pattern_default or "U")

        if not syllables:
            return None

        meter_name, best_score, debug_scores, resolved_pattern = self._best_meter_for_ambiguous_syllables(
            syllables,
            context=context,
        )
        if len(resolved_pattern) != len(syllables):
            resolved_pattern = "".join(unit.default_stress for unit in syllables)

        context_bonus = float(debug_scores.get("context_bonus") or 0.0)
        output_pattern_chars: List[str] = []
        for idx, unit in enumerate(syllables):
            chosen = resolved_pattern[idx]
            if chosen != unit.default_stress:
                option_costs = {stress: cost for stress, cost in unit.options}
                flip_cost = float(option_costs.get(chosen, POLY_FLIP_COST))
                # Keep meter-aware flips mostly for low-cost ambiguity (typically monosyllables).
                # For high-cost flips, fall back to lexical default unless context prior is strong.
                if flip_cost >= 1.0 and context_bonus < 0.08:
                    chosen = unit.default_stress
            output_pattern_chars.append(chosen)
        output_pattern = "".join(output_pattern_chars)

        syllable_positions: List[Tuple[str, bool]] = []
        syllable_char_spans: List[Tuple[int, int]] = []
        token_patterns_resolved: List[str] = ["" for _ in token_patterns]
        for idx, unit in enumerate(syllables):
            stressed = output_pattern[idx] == "S"
            syllable_positions.append((unit.text, stressed))
            syllable_char_spans.append((unit.char_start, unit.char_end))
            if 0 <= unit.token_index < len(token_patterns_resolved):
                token_patterns_resolved[unit.token_index] += output_pattern[idx]

        token_patterns_out = [pat or "U" for pat in token_patterns_resolved]
        stress_pattern = "".join(token_patterns_out)

        parsed = self._parse_meter_name(meter_name)
        feet_count = parsed[1] if parsed else max(1, round(len(stress_pattern) / 2))
        confidence = max(0.0, min(1.0, best_score))

        return LineAnalysis(
            line_no=line_no,
            source_text=line,
            tokens=tokens,
            stress_pattern=stress_pattern,
            meter_name=meter_name,
            feet_count=feet_count,
            confidence=confidence,
            debug_scores=debug_scores,
            token_patterns=token_patterns_out,
            syllable_positions=syllable_positions,
            syllable_char_spans=syllable_char_spans,
        )
