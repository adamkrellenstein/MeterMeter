from html import escape


def stress_to_glyphs(pattern: str) -> str:
    return " ".join("/" if ch == "S" else "˘" for ch in pattern)


def confidence_band(confidence: float) -> str:
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def render_analysis_html(
    analysis,
    show_confidence: bool = True,
    show_hint: bool = True,
    inline_mode: bool = False,
) -> str:
    scansion = stress_to_glyphs(analysis.stress_pattern)
    meter_name = escape(analysis.meter_name)
    conf_pct = int(round(max(0.0, min(1.0, analysis.confidence)) * 100.0))
    band = confidence_band(analysis.confidence)

    conf_html = f"<span class='conf'>{conf_pct}%</span>" if show_confidence else ""
    hint_text = getattr(analysis, "analysis_hint", "") or ""
    hint_html = ""
    if show_hint and hint_text and not inline_mode:
        hint_html = f"<div class='hint'>{escape(hint_text)}</div>"

    if inline_mode:
        return (
            "<body id='poetry-meter'>"
            "<style>"
            "body#poetry-meter { margin: 0; padding: 0; }"
            ".inline {"
            "  font-family: var(--font_monospace, Menlo, Monaco, monospace);"
            "  font-size: 0.95em;"
            "  color: color(var(--foreground) alpha(0.48));"
            "  font-style: normal;"
            "  font-weight: 400;"
            "  white-space: nowrap;"
            "}"
            ".meta { margin-left: 0.38rem; }"
            ".meter { margin-right: 0.18rem; }"
            ".conf { margin-left: 0.12rem; font-weight: 500; }"
            "</style>"
            "<span class='inline'>"
            f"<span class='meta'><span class='meter'>{meter_name}</span> {conf_html}</span>"
            "</span>"
            "</body>"
        )

    return (
        "<body id='poetry-meter'>"
        "<style>"
        "body#poetry-meter { margin: 0; padding: 0; }"
        ".row {"
        "  font-family: Georgia, 'Iowan Old Style', serif;"
        "  font-size: 0.82rem;"
        "  opacity: 0.92;"
        "  padding: 0.1rem 0.42rem;"
        "  border-radius: 0.3rem;"
        "}"
        ".stressline {"
        "  font-family: Menlo, Monaco, monospace;"
        "  letter-spacing: 0.06rem;"
        "  font-size: 0.92rem;"
        "  font-weight: 700;"
        "}"
        ".metaline { margin-top: 0.06rem; opacity: 0.9; }"
        ".meter { margin-right: 0.35rem; font-style: italic; }"
        ".conf { font-weight: 600; }"
        ".hint { margin-top: 0.1rem; opacity: 0.86; }"
        ".high { background-color: color(var(--greenish) alpha(0.11)); }"
        ".medium { background-color: color(var(--yellowish) alpha(0.13)); }"
        ".low { background-color: color(var(--reddish) alpha(0.12)); }"
        "</style>"
        f"<div class='row {band}'>"
        f"<div class='stressline'>{escape(scansion)}</div>"
        f"<div class='metaline'><span class='meter'>{meter_name}</span>{conf_html}</div>"
        f"{hint_html}"
        "</div>"
        "</body>"
    )
