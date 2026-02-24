import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import prosodic

TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

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
    "round", # 100% S
    "here",  # 100% S
    "own",   # 100% S
    "each",  # 89% S
    "there", # 83% S
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



class MeterEngine:
    def __init__(self) -> None:
        pass

    def tokenize(self, line: str) -> List[str]:
        return TOKEN_RE.findall(line)

    # -- Template-matching helpers --

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
        if 9 <= len(pattern) <= 11:
            if foot_name == "iambic" and feet == 5:
                score += IAMBIC_PENTAMETER_BONUS
            elif foot_name == "trochaic" and feet == 5:
                score += TROCHAIC_PENTAMETER_BONUS
            elif foot_name in {"anapestic", "dactylic"}:
                score -= TERNARY_METER_PENALTY
        if 12 <= len(pattern) <= 13:
            if foot_name == "iambic" and feet == 6:
                score += IAMBIC_HEXAMETER_BONUS
            elif foot_name in {"anapestic", "dactylic"}:
                score -= TERNARY_METER_PENALTY
        return max(0.0, min(1.0, score))

    def _meter_candidates(self, pattern: str) -> List[Tuple[str, int, float]]:
        if not pattern:
            return []
        candidates: List[Tuple[str, int, float]] = []
        syllables = len(pattern)
        for foot_name, template_unit in FOOT_TEMPLATES.items():
            unit = len(template_unit)
            approx_feet = max(1, int(round(syllables / float(unit))))
            for feet in range(max(1, approx_feet - 1), min(6, approx_feet + 1) + 1):
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
        if 9 <= len(pattern) <= 11:
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

    def analyze_line(self, line: str, line_no: int = 0) -> Optional[LineAnalysis]:
        if not line.strip():
            return None
        tokens = self.tokenize(line)
        if not tokens:
            return None

        try:
            ptext = prosodic.Text(line)
            if not ptext.lines:
                return None
            pline = ptext.lines[0]
        except Exception:
            return None

        # Build stress from lexical pronunciation (CMU Dict / eSpeak),
        # not from the OT metrical parse.  Monosyllabic function words
        # are forced unstressed following Groves' rules.
        syllable_positions: List[Tuple[str, bool]] = []
        token_patterns: List[str] = []

        for wt in pline.wordtokens:
            wtype = wt.wordtype
            if getattr(wtype, "is_punc", False):
                continue
            wf = wtype.form  # first (least-stressed) pronunciation variant
            syls = getattr(wf, "syllables", None)
            if not syls:
                continue

            word_text = wt.txt.strip().lower().strip("'")
            is_mono = len(syls) == 1
            pat = ""
            for syl in syls:
                if is_mono and word_text in UNSTRESSED_MONOSYLLABLES:
                    stressed = False
                elif is_mono and word_text in STRESSED_MONOSYLLABLES:
                    stressed = True
                else:
                    stressed = getattr(syl, "is_stressed", False)
                syllable_positions.append((syl.txt.lower(), stressed))
                pat += "S" if stressed else "U"
            token_patterns.append(pat)

        if not syllable_positions:
            return None

        # If prosodic gave a different word count than TOKEN_RE, align to TOKEN_RE.
        if len(token_patterns) != len(tokens):
            all_syls_flat = syllable_positions
            token_patterns = []
            n_tokens = len(tokens)
            n_pos = len(all_syls_flat)
            for i in range(n_tokens):
                start = (i * n_pos) // n_tokens
                end = ((i + 1) * n_pos) // n_tokens
                if end <= start:
                    end = min(n_pos, start + 1)
                pat = "".join("S" if all_syls_flat[j][1] else "U" for j in range(start, end))
                token_patterns.append(pat or "U")

        stress_pattern = "".join(token_patterns)

        meter_name, best_score, debug_scores = self.best_meter_for_stress_pattern(stress_pattern)
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
            token_patterns=token_patterns,
            syllable_positions=syllable_positions,
        )
