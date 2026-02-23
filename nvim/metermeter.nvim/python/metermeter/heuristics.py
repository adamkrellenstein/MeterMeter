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

# Broader set including archaic forms and auxiliaries that may carry stress
# contextually but should have flexible stress assignment in meter fitting.
FUNCTION_WORDS = UNSTRESSED_FUNCTION_WORDS | {
    "can",
    "do",
    "hath",
    "not",
    "shall",
    "thee",
    "thou",
    "thy",
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

ELIDED_SYLLABLE_OVERRIDES = {
    "heaven": 1,
    "even": 1,
    "every": 2,
    "oer": 1,
    "o'er": 1,
    "power": 1,
    "hour": 1,
    "flower": 1,
    "fiery": 2,
    "spirit": 2,
}

# Adjectives where "-ed" is traditionally syllabic even after non-t/d consonants.
SYLLABIC_ED_ADJECTIVES = {
    "aged", "beloved", "blessed", "crabbed", "crooked", "cursed",
    "dogged", "jagged", "learned", "naked", "ragged", "rugged",
    "sacred", "wicked", "winged", "wretched",
}


def clean_word(word: str) -> str:
    return NON_ALPHA_RE.sub("", word.lower()).strip("'")


def estimate_syllables(word: str) -> int:
    clean = clean_word(word)
    if not clean:
        return 0

    override = ELIDED_SYLLABLE_OVERRIDES.get(clean)
    if isinstance(override, int) and override > 0:
        return override

    groups = VOWEL_GROUP_RE.findall(clean)
    syllables = len(groups)

    if clean.endswith("e") and not clean.endswith(("le", "ye")) and syllables > 1:
        syllables -= 1
    # Silent past-tense "-ed": the "e" in "-ed" is silent after consonants
    # other than t/d (e.g., "entwined" = 2 syl, not 3). Exceptions:
    # - Adjectives with syllabic "-ed" (e.g., "naked", "wicked").
    # - Consonant + "led" (single L): the "e" is part of syllabic "-le"
    #   from the base word (e.g., "trampled" from "trample").
    elif (
        clean.endswith("ed")
        and len(clean) >= 4
        and syllables > 1
        and clean[-3] not in "aeiouy"
        and clean[-3] not in "td"
        and clean not in SYLLABIC_ED_ADJECTIVES
        and not (
            clean.endswith("led")
            and not clean.endswith("lled")
            and len(clean) >= 5
            and clean[-4] not in "aeiouy"
        )
    ):
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
