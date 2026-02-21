import re

VOWEL_GROUP_RE = re.compile(r"[aeiouy]+", re.IGNORECASE)
NON_ALPHA_RE = re.compile(r"[^a-z']+")

UNSTRESSED_FUNCTION_WORDS = {
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
}

STRESSED_SUFFIXES = (
    "tion",
    "sion",
    "cian",
    "ture",
    "gion",
    "self",
    "selves",
    "ique",
    "eer",
)

WEAK_SUFFIXES = (
    "ing",
    "ed",
    "es",
    "ly",
    "er",
    "est",
    "ous",
    "less",
    "ness",
)


def clean_word(word: str) -> str:
    return NON_ALPHA_RE.sub("", word.lower()).strip("'")


def estimate_syllables(word: str) -> int:
    clean = clean_word(word)
    if not clean:
        return 0

    groups = VOWEL_GROUP_RE.findall(clean)
    syllables = len(groups)

    if clean.endswith("e") and not clean.endswith(("le", "ye")) and syllables > 1:
        syllables -= 1

    return max(1, syllables)


def _build_pattern(syllables: int, stress_index: int) -> str:
    if syllables <= 0:
        return ""
    stress_index = max(0, min(stress_index, syllables - 1))
    pattern = ["U"] * syllables
    pattern[stress_index] = "S"
    return "".join(pattern)


def estimate_stress_pattern(word: str) -> str:
    clean = clean_word(word)
    if not clean:
        return ""

    syllables = estimate_syllables(clean)

    if clean in UNSTRESSED_FUNCTION_WORDS:
        if syllables == 1:
            return "U"
        return _build_pattern(syllables, syllables - 1)

    if syllables == 1:
        return "S"

    if clean.endswith(STRESSED_SUFFIXES):
        return _build_pattern(syllables, syllables - 1)

    if clean.endswith(WEAK_SUFFIXES) and syllables >= 2:
        return _build_pattern(syllables, max(0, syllables - 2))

    return _build_pattern(syllables, 0)

