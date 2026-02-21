import os
import json
import re
import shutil
import subprocess
import threading
import traceback
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Set, Tuple

import sublime
import sublime_plugin

try:
    from .llm_bootstrap import BootstrapError, bootstrap_local_llm
    from .llm_setup_catalog import SetupModelProfile, hardware_options, model_profiles_for_hardware
    from .llm_sidecar import LLMSidecarManager, SidecarConfig, sidecar_config_from_settings
    from .meter_engine import LineAnalysis, MeterEngine
    from .llm_refiner import LLMRefiner
    from .phantom_view import render_analysis_html
except ImportError:
    from llm_bootstrap import BootstrapError, bootstrap_local_llm
    from llm_setup_catalog import SetupModelProfile, hardware_options, model_profiles_for_hardware
    from llm_sidecar import LLMSidecarManager, SidecarConfig, sidecar_config_from_settings
    from meter_engine import LineAnalysis, MeterEngine
    from llm_refiner import LLMRefiner
    from phantom_view import render_analysis_html

PACKAGE_SETTINGS = "PoetryMeter.sublime-settings"
VIEW_ENABLED_KEY = "poetry_meter.live_enabled"
PHANTOM_KEY = "poetry_meter.inline"
STRESS_REGION_KEY = "poetry_meter.stress"
OVERLAY_SCHEME_FILENAME = "PoetryMeterOverlay.sublime-color-scheme"
PLUGIN_VERSION = "dev-2026-02-21-debug-loop-v2"
WORD_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
VOWEL_GROUP_RE = re.compile(r"[AEIOUYaeiouy]+")
STRESS_SCOPE_CANDIDATES = [
    "region.blackish",
    "region.grayish",
    "region.bluish",
    "region.cyanish",
    "region.greenish",
    "region.yellowish",
    "region.orangish",
    "region.redish",
    "region.purplish",
]
TYPST_POETRY_BLOCK_STARTERS = ("#stanza[", "#couplet[", "#poem[")

_VIEW_STATES: Dict[int, "ViewState"] = {}
_ENGINE: Optional[MeterEngine] = None
_ENGINE_DICT_PATH: Optional[str] = None
_LLM_REFINER: Optional[LLMRefiner] = None
_LLM_REFINER_CFG: Optional[Tuple[str, str, str, str, int]] = None
_LLM_SIDECAR: Optional[LLMSidecarManager] = None
_LLM_SIDECAR_CFG: Optional[SidecarConfig] = None
_ENGINE_LOCK = threading.Lock()
_LLM_LOCK = threading.Lock()
_BACKGROUND_TICK_ACTIVE = False
_LAST_SIDECAR_FALLBACK_ERR: str = ""
_LOG_THROTTLE_AT: Dict[str, float] = {}
_DEBUG_LAST_BUNDLE_AT: float = 0.0
_DEBUG_CONTROL_LAST_MTIME: float = 0.0


class ViewState:
    def __init__(self, view: sublime.View, enabled: bool):
        self.view_id = view.id()
        self.view = view
        self.enabled = enabled
        self.generation = 0
        self.last_line_count = 0
        self.analyses: Dict[int, LineAnalysis] = {}
        self.phantom_set = sublime.PhantomSet(view, PHANTOM_KEY)

    def next_generation(self) -> int:
        self.generation += 1
        return self.generation


def _settings() -> sublime.Settings:
    return sublime.load_settings(PACKAGE_SETTINGS)


def _debug_enabled() -> bool:
    try:
        settings = _settings()
        if bool(settings.get("debug_log", False)):
            return True
        path = str(settings.get("debug_force_enable_file", "/tmp/poetrymeter_debug_on") or "").strip()
        if path:
            return os.path.exists(os.path.expanduser(path))
        return False
    except Exception:
        return False


def _log_file_path() -> str:
    try:
        path = str(_settings().get("debug_log_file", "/tmp/poetrymeter.log") or "").strip()
    except Exception:
        path = "/tmp/poetrymeter.log"
    if not path:
        return ""
    return os.path.expanduser(path)

def _debug_bundle_path() -> str:
    try:
        path = str(_settings().get("debug_bundle_file", "/tmp/poetrymeter_bundle.json") or "").strip()
    except Exception:
        path = "/tmp/poetrymeter_bundle.json"
    return os.path.expanduser(path) if path else ""


def _debug_render_path() -> str:
    try:
        path = str(_settings().get("debug_render_file", "/tmp/poetrymeter_render.txt") or "").strip()
    except Exception:
        path = "/tmp/poetrymeter_render.txt"
    return os.path.expanduser(path) if path else ""

def _debug_control_path() -> str:
    try:
        path = str(_settings().get("debug_control_file", "/tmp/poetrymeter_control.json") or "").strip()
    except Exception:
        path = "/tmp/poetrymeter_control.json"
    return os.path.expanduser(path) if path else ""

def _debug_control_state_path() -> str:
    try:
        path = str(_settings().get("debug_control_state_file", "/tmp/poetrymeter_control_state.json") or "").strip()
    except Exception:
        path = "/tmp/poetrymeter_control_state.json"
    return os.path.expanduser(path) if path else ""


def _path_with_reason(path: str, reason: str) -> str:
    # /tmp/foo.json + reason=manual -> /tmp/foo.manual.json
    if not path or not reason:
        return path
    base, ext = os.path.splitext(path)
    if not ext:
        return base + "." + reason
    return base + "." + reason + ext

def _read_control_state() -> int:
    path = _debug_control_state_path()
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            val = data.get("last_handled_id", 0)
            if isinstance(val, int):
                return val
    except Exception:
        return 0
    return 0


def _write_control_state(last_id: int) -> None:
    path = _debug_control_state_path()
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"last_handled_id": int(last_id)}, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        return


def _log_throttled(key: str, msg: str) -> None:
    if not _debug_enabled():
        return
    settings = _settings()
    throttle_ms = int(settings.get("debug_log_throttle_ms", 0))
    if throttle_ms <= 0:
        _log(msg)
        return
    now = time.time()
    last = _LOG_THROTTLE_AT.get(key, 0.0)
    if (now - last) * 1000.0 < float(throttle_ms):
        return
    _LOG_THROTTLE_AT[key] = now
    _log(msg)


def _log(msg: str) -> None:
    if not _debug_enabled():
        return

    line = "[PoetryMeter] {} {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), msg)

    try:
        print(line)
    except Exception:
        pass

    path = _log_file_path()
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # Basic rotation: keep file from growing unbounded.
        try:
            if os.path.exists(path) and os.path.getsize(path) > 1024 * 1024:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        # Logging should never break the plugin.
        return


def _log_force(msg: str) -> None:
    # For explicit "Dump Debug Info" calls: write to the configured log file even if debug is off.
    line = "[PoetryMeter] {} {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line)
    except Exception:
        pass
    path = _log_file_path()
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        return

def _engine() -> MeterEngine:
    global _ENGINE
    global _ENGINE_DICT_PATH

    settings = _settings()
    override = settings.get("dictionary_path_override")
    dict_path = None
    if isinstance(override, str) and override.strip():
        dict_path = os.path.expanduser(override.strip())

    if _ENGINE is not None and dict_path == _ENGINE_DICT_PATH:
        return _ENGINE

    with _ENGINE_LOCK:
        if _ENGINE is None or dict_path != _ENGINE_DICT_PATH:
            _ENGINE = MeterEngine(dict_path=dict_path)
            _ENGINE_DICT_PATH = dict_path
    return _ENGINE


def _sidecar_manager() -> Optional[LLMSidecarManager]:
    global _LLM_SIDECAR
    global _LLM_SIDECAR_CFG

    settings = _settings()
    cfg = sidecar_config_from_settings(settings)

    if not cfg.enabled:
        if _LLM_SIDECAR is not None:
            _LLM_SIDECAR.stop()
        _LLM_SIDECAR = None
        _LLM_SIDECAR_CFG = None
        return None

    if _LLM_SIDECAR is not None and cfg == _LLM_SIDECAR_CFG:
        return _LLM_SIDECAR

    with _LLM_LOCK:
        if _LLM_SIDECAR is not None and cfg != _LLM_SIDECAR_CFG:
            _LLM_SIDECAR.stop()
        if _LLM_SIDECAR is None or cfg != _LLM_SIDECAR_CFG:
            _LLM_SIDECAR = LLMSidecarManager(cfg)
            _LLM_SIDECAR_CFG = cfg
    return _LLM_SIDECAR


def _resolve_llm_endpoint() -> str:
    settings = _settings()
    sidecar = _sidecar_manager()
    if sidecar is not None:
        endpoint = sidecar.ensure_running()
        if endpoint:
            return endpoint

        # KISS: if sidecar can't start, fall back to a manually-managed endpoint.
        # This lets Ollama "just work" even if sidecar settings are present/misconfigured.
        fallback = str(settings.get("llm_endpoint", "") or "").strip()
        if fallback:
            global _LAST_SIDECAR_FALLBACK_ERR
            err = ""
            try:
                err = str(sidecar.last_error or "").strip()
            except Exception:
                err = ""
            if err and err != _LAST_SIDECAR_FALLBACK_ERR:
                _LAST_SIDECAR_FALLBACK_ERR = err
                _log("llm sidecar failed ({!r}); falling back to llm_endpoint".format(err))
            return fallback

        return ""
    return str(settings.get("llm_endpoint", "") or "").strip()


def _llm_refiner() -> Optional[LLMRefiner]:
    global _LLM_REFINER
    global _LLM_REFINER_CFG

    settings = _settings()
    if not bool(settings.get("llm_enabled", True)):
        _log("llm disabled via settings")
        return None

    endpoint = _resolve_llm_endpoint()
    model = str(settings.get("llm_model", "") or "").strip()
    api_key = str(settings.get("llm_api_key", "") or "").strip()
    prompt_version = str(settings.get("llm_prompt_version", "v2") or "v2").strip()
    error_cooldown_ms = int(settings.get("llm_error_cooldown_ms", 3000))

    if not endpoint or not model:
        _log("llm not configured (endpoint={!r} model={!r})".format(endpoint, model))
        return None

    cfg = (endpoint, model, api_key, prompt_version, error_cooldown_ms)
    if _LLM_REFINER is not None and cfg == _LLM_REFINER_CFG:
        return _LLM_REFINER

    with _LLM_LOCK:
        if _LLM_REFINER is None or cfg != _LLM_REFINER_CFG:
            _LLM_REFINER = LLMRefiner(
                endpoint=endpoint,
                model=model,
                api_key=api_key,
                prompt_version=prompt_version,
                error_cooldown_ms=error_cooldown_ms,
                logger=lambda m: _log("llm: " + m),
            )
            _LLM_REFINER_CFG = cfg
    return _LLM_REFINER


def _line_count(view: sublime.View) -> int:
    row, _ = view.rowcol(view.size())
    return row + 1


def _line_region(view: sublime.View, line_no: int) -> Optional[sublime.Region]:
    if line_no < 0:
        return None
    if line_no >= _line_count(view):
        return None
    point = view.text_point(line_no, 0)
    return view.line(point)


def _selected_line_numbers(view: sublime.View) -> Set[int]:
    line_numbers: Set[int] = set()
    for region in view.sel():
        start_row, _ = view.rowcol(region.begin())
        end_row, _ = view.rowcol(region.end())
        for row in range(start_row, end_row + 1):
            line_numbers.add(row)

    if line_numbers:
        return line_numbers

    row, _ = view.rowcol(0)
    return {row}


def _visible_line_numbers(view: sublime.View) -> Set[int]:
    visible = view.visible_region()
    start_row, _ = view.rowcol(visible.begin())
    end_row, _ = view.rowcol(visible.end())
    return set(range(start_row, end_row + 1))


def _typst_poetry_lines_upto(view: sublime.View, max_row: int) -> Set[int]:
    """
    For Typst files, only annotate poetry lines inside common block constructs like:
      #stanza[ ... ]
      #couplet[ ... ]

    This avoids annotating Typst directives (#import/#show) and metadata fields.
    """
    max_row = max(0, min(int(max_row), _line_count(view) - 1))
    allowed: Set[int] = set()

    in_poetry = False
    depth = 0

    for row in range(0, max_row + 1):
        pt = view.text_point(row, 0)
        raw = view.substr(view.line(pt))
        stripped = raw.strip()
        low = stripped.lower()

        if not in_poetry:
            for starter in TYPST_POETRY_BLOCK_STARTERS:
                if low.startswith(starter):
                    in_poetry = True
                    # Count bracket depth on the starter line.
                    delta = raw.count("[") - raw.count("]")
                    depth = delta if delta > 0 else 1
                    break
            continue

        # We are inside a poetry block. Update depth based on brackets.
        depth += raw.count("[") - raw.count("]")
        if depth <= 0:
            in_poetry = False
            depth = 0
            continue

        # Skip block delimiters and directives even if encountered inside.
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("]"):
            continue
        if stripped.startswith("{") or stripped.startswith("}"):
            continue

        allowed.add(row)

    return allowed


def _is_supported_view(view: sublime.View) -> bool:
    if view is None:
        return False
    if view.settings().get("is_widget"):
        return False

    def _normalized_exts(key: str, default: List[str]) -> Set[str]:
        raw = _settings().get(key, default)
        if not isinstance(raw, list):
            return set()
        out = set()
        for ext in raw:
            if not isinstance(ext, str):
                continue
            value = ext.strip().lower()
            if not value:
                continue
            if not value.startswith("."):
                value = "." + value
            out.add(value)
        return out

    def _marker_state_for_view(v: sublime.View) -> Optional[bool]:
        """
        Return True if file opts-in, False if opts-out, or None if neither marker is found.

        Markers are scanned only in the first N lines for speed.
        """
        settings = _settings()
        max_lines = int(settings.get("marker_scan_max_lines", 25))
        if max_lines <= 0:
            return None
        opt_in = str(settings.get("opt_in_marker", "poetrymeter: on") or "").strip().lower()
        opt_out = str(settings.get("opt_out_marker", "poetrymeter: off") or "").strip().lower()
        if not opt_in and not opt_out:
            return None

        try:
            total = _line_count(v)
            limit = min(total, max_lines)
            for row in range(limit):
                pt = v.text_point(row, 0)
                line = v.substr(v.line(pt)).strip().lower()
                if opt_out and opt_out in line:
                    return False
                if opt_in and opt_in in line:
                    return True
        except Exception:
            return None
        return None

    settings = _settings()
    filename = view.file_name() or ""
    _, ext = os.path.splitext(filename.lower())

    # Markers can force enable/disable regardless of extension lists.
    marker_state = _marker_state_for_view(view)
    if marker_state is False:
        return False

    enabled_exts = _normalized_exts("enabled_file_extensions", [".poem"])
    opt_in_exts = _normalized_exts("opt_in_file_extensions", [".typ"])
    allowed_via_marker = (ext in opt_in_exts and marker_state is True)

    if enabled_exts:
        if ext in enabled_exts:
            pass
        else:
            # Allow opt-in extensions only when the file contains the opt-in marker.
            if allowed_via_marker:
                pass
            else:
                return False

    # For opt-in files, trust the marker and skip syntax checks.
    if allowed_via_marker:
        return True

    selector = str(
        settings.get(
            "syntax_selector",
            "text.plain, text.html.markdown, text.orgmode, text.restructuredtext, source.typst",
        )
        or ""
    ).strip()
    if selector:
        point = 0 if view.size() == 0 else min(view.size() - 1, 0)
        if view.score_selector(point, selector) == 0:
            return False
    return True


def _ensure_state(view: sublime.View) -> ViewState:
    state = _VIEW_STATES.get(view.id())
    if state is not None:
        return state

    settings = _settings()
    if view.settings().get(VIEW_ENABLED_KEY) is None:
        view.settings().set(VIEW_ENABLED_KEY, bool(settings.get("enabled_by_default", True)))

    enabled = bool(view.settings().get(VIEW_ENABLED_KEY, True))
    _recover_overlay_color_scheme(view)
    state = ViewState(view, enabled=enabled)
    state.last_line_count = _line_count(view)
    _VIEW_STATES[view.id()] = state
    return state


def _resolve_full_scan_lines(view: sublime.View, settings: sublime.Settings) -> Set[int]:
    total_lines = _line_count(view)
    max_live_lines = int(settings.get("max_buffer_lines_for_live", 10000))
    visible_only = bool(settings.get("scan_visible_only_when_large", True))

    if visible_only and total_lines > max_live_lines:
        return _visible_line_numbers(view)

    return set(range(total_lines))


def _feet_count_from_meter_name(meter_name: str) -> int:
    line_name = meter_name.rsplit(" ", 1)[-1].strip().lower()
    feet_map = {
        "monometer": 1,
        "dimeter": 2,
        "trimeter": 3,
        "tetrameter": 4,
        "pentameter": 5,
        "hexameter": 6,
    }
    return feet_map.get(line_name, 0)


def _merge_llm_refinement(
    analysis: LineAnalysis,
    llm_refinement,
    override_meter: bool,
    override_stress: bool,
) -> LineAnalysis:
    stress_pattern = analysis.stress_pattern
    meter_name = analysis.meter_name
    confidence = analysis.confidence
    feet_count = analysis.feet_count
    token_patterns = analysis.token_patterns

    if override_stress:
        stress_pattern = llm_refinement.stress_pattern
        if llm_refinement.token_patterns:
            token_patterns = llm_refinement.token_patterns
        else:
            split_patterns = _split_stress_by_baseline(stress_pattern, analysis.token_patterns)
            if split_patterns:
                token_patterns = split_patterns

    if override_meter:
        meter_name = llm_refinement.meter_name
        confidence = llm_refinement.confidence
        inferred_feet = _feet_count_from_meter_name(meter_name)
        if inferred_feet > 0:
            feet_count = inferred_feet

    return LineAnalysis(
        line_no=analysis.line_no,
        source_text=analysis.source_text,
        tokens=analysis.tokens,
        stress_pattern=stress_pattern,
        meter_name=meter_name,
        feet_count=feet_count,
        confidence=confidence,
        oov_tokens=analysis.oov_tokens,
        debug_scores=analysis.debug_scores,
        token_patterns=token_patterns,
        analysis_hint=llm_refinement.analysis_hint,
        source="llm",
    )


def _split_stress_by_baseline(stress_pattern: str, baseline_patterns: List[str]) -> List[str]:
    if not stress_pattern or not baseline_patterns:
        return []
    if any(ch not in {"U", "S"} for ch in stress_pattern):
        return []

    out: List[str] = []
    idx = 0
    for pattern in baseline_patterns:
        if not isinstance(pattern, str) or not pattern:
            return []
        width = len(pattern)
        if idx + width > len(stress_pattern):
            return []
        out.append(stress_pattern[idx : idx + width])
        idx += width

    if idx != len(stress_pattern):
        return []
    return out


def _even_spans(length: int, target: int) -> List[Tuple[int, int]]:
    if length <= 0:
        return []
    target = max(1, min(target, length))
    out: List[Tuple[int, int]] = []
    for idx in range(target):
        start = int((idx * length) / float(target))
        end = int(((idx + 1) * length) / float(target))
        if end <= start:
            end = min(length, start + 1)
        out.append((start, end))
    return out


def _syllable_spans(token: str, syllable_count: int) -> List[Tuple[int, int]]:
    token_len = len(token)
    if token_len <= 0 or syllable_count <= 0:
        return []
    if syllable_count == 1:
        return [(0, token_len)]

    groups = list(VOWEL_GROUP_RE.finditer(token))
    if len(groups) < 2:
        return _even_spans(token_len, syllable_count)

    spans: List[Tuple[int, int]] = []
    current_start = 0
    for idx in range(len(groups) - 1):
        left_end = groups[idx].end()
        right_start = groups[idx + 1].start()
        split = left_end if right_start <= left_end else (left_end + right_start) // 2
        if split <= current_start:
            split = min(token_len, current_start + 1)
        spans.append((current_start, split))
        current_start = split
    spans.append((current_start, token_len))

    if len(spans) != syllable_count:
        return _even_spans(token_len, syllable_count)
    return spans


def _schedule_scan(
    view: sublime.View,
    state: ViewState,
    line_numbers: Optional[Set[int]] = None,
    force_full: bool = False,
    debounce_override: Optional[int] = None,
) -> None:
    if not _is_supported_view(view):
        return
    if not state.enabled:
        return

    settings = _settings()
    debounce_ms = int(settings.get("debounce_ms", 50)) if debounce_override is None else debounce_override
    generation = state.next_generation()

    def run() -> None:
        if generation != state.generation:
            return
        _scan_and_apply(view, state, generation, line_numbers=line_numbers, force_full=force_full)

    sublime.set_timeout_async(run, max(0, debounce_ms))


def _scan_and_apply(
    view: sublime.View,
    state: ViewState,
    generation: int,
    line_numbers: Optional[Set[int]] = None,
    force_full: bool = False,
) -> None:
    if generation != state.generation:
        return
    if not state.enabled:
        return
    if not _is_supported_view(view):
        return

    settings = _settings()
    filename = view.file_name() or ""
    _, file_ext = os.path.splitext(filename.lower())
    max_line_length = int(settings.get("max_line_length", 220))
    ignore_prefixes_raw = settings.get("ignore_line_prefixes", ["//"])
    ignore_prefixes = []
    if isinstance(ignore_prefixes_raw, list):
        ignore_prefixes = [str(prefix).strip() for prefix in ignore_prefixes_raw if str(prefix).strip()]
    llm_refiner = _llm_refiner()
    llm_timeout_ms = int(settings.get("llm_timeout_ms", 1000))
    llm_temperature = float(settings.get("llm_temperature", 0.1))
    llm_max_lines = max(0, int(settings.get("llm_max_lines_per_scan", 1)))
    llm_override_meter = bool(settings.get("llm_override_meter", True))
    llm_override_stress = bool(settings.get("llm_override_stress", True))
    llm_remaining = llm_max_lines

    if force_full:
        target_lines = _resolve_full_scan_lines(view, settings)
    else:
        target_lines = line_numbers or _selected_line_numbers(view)

    if not target_lines:
        return

    target_lines = set(target_lines)
    _log_throttled(
        "scan:{}:{}".format(view.id(), 1 if force_full else 0),
        "scan view_id={} force_full={} target_lines={} llm_max_lines={}".format(
            view.id(),
            bool(force_full),
            len(target_lines),
            llm_max_lines,
        ),
    )
    analyses = dict(state.analyses)
    meter = _engine()

    llm_candidates: List[LineAnalysis] = []
    analyzed_count = 0
    ignored_count = 0
    removed_count = 0

    ordered_lines = sorted(target_lines)

    typst_allowed_lines: Optional[Set[int]] = None
    if file_ext == ".typ":
        try:
            max_row = max(ordered_lines) if ordered_lines else 0
            if analyses:
                try:
                    max_row = max(max_row, max(int(k) for k in analyses.keys()))
                except Exception:
                    pass
            typst_allowed_lines = _typst_poetry_lines_upto(view, max_row)
            _log_throttled(
                "typst_filter:{}".format(view.id()),
                "typst filter view_id={} allowed_lines={} max_row={}".format(
                    view.id(),
                    len(typst_allowed_lines),
                    max_row,
                )
            )
            # Clear stale analyses outside poetry blocks even when we're only scanning a subset of lines.
            if typst_allowed_lines is not None and analyses:
                for ln in list(analyses.keys()):
                    if int(ln) not in typst_allowed_lines:
                        analyses.pop(ln, None)
        except Exception as exc:
            # If the filter fails, err on the side of annotating nothing (not Typst code).
            _log("typst filter exception: {}".format(repr(exc)))
            typst_allowed_lines = set()

    if llm_refiner is not None and llm_max_lines > 0:
        # Prefer refining visible lines first so "rely on LLM" feels consistent even when
        # the engine does a full-buffer scan.
        try:
            visible = _visible_line_numbers(view)
        except Exception:
            visible = set()
        if visible:
            visible_ordered = sorted(target_lines.intersection(visible))
            if visible_ordered:
                rest = sorted(target_lines.difference(visible))
                ordered_lines = visible_ordered + rest

    for line_no in ordered_lines:
        region = _line_region(view, line_no)
        if region is None:
            analyses.pop(line_no, None)
            removed_count += 1
            continue

        raw_line = view.substr(region)
        stripped = raw_line.strip()
        if typst_allowed_lines is not None and file_ext == ".typ":
            if line_no not in typst_allowed_lines:
                analyses.pop(line_no, None)
                ignored_count += 1
                continue
        if not raw_line.strip() or len(raw_line) > max_line_length:
            analyses.pop(line_no, None)
            ignored_count += 1
            continue
        if ignore_prefixes and any(stripped.startswith(prefix) for prefix in ignore_prefixes):
            analyses.pop(line_no, None)
            ignored_count += 1
            continue

        analysis = meter.analyze_line(raw_line, line_no=line_no)
        if analysis is None:
            analyses.pop(line_no, None)
            ignored_count += 1
        else:
            analyses[line_no] = analysis
            analyzed_count += 1
            if llm_refiner is not None and llm_remaining > 0:
                llm_candidates.append(analysis)
                llm_remaining -= 1

    _log_throttled(
        "scan_details:{}".format(view.id()),
        "scan details view_id={} analyzed={} ignored={} removed={} llm_candidates={}".format(
            view.id(),
            analyzed_count,
            ignored_count,
            removed_count,
            len(llm_candidates),
        ),
    )

    if llm_refiner is not None and llm_candidates:
        try:
            refined_by_line = llm_refiner.refine_lines(
                llm_candidates,
                timeout_ms=llm_timeout_ms,
                temperature=llm_temperature,
            )
        except Exception:
            _log("llm refine_lines exception: {}".format(traceback.format_exc().strip()))
            refined_by_line = {}

        _log("llm refine_lines returned {} lines".format(len(refined_by_line)))
        if not refined_by_line:
            _maybe_switch_to_installed_ollama_model(settings, llm_refiner)
        for baseline in llm_candidates:
            refinement = refined_by_line.get(baseline.line_no)
            if refinement is None:
                continue
            merged = _merge_llm_refinement(
                analysis=baseline,
                llm_refinement=refinement,
                override_meter=llm_override_meter,
                override_stress=llm_override_stress,
            )
            analyses[baseline.line_no] = merged

    def apply_to_ui() -> None:
        if generation != state.generation:
            return
        if not state.enabled:
            return
        state.analyses = analyses
        state.last_line_count = _line_count(view)
        _refresh_phantoms(view, state)

    sublime.set_timeout(apply_to_ui, 0)


def _refresh_phantoms(view: sublime.View, state: ViewState) -> None:
    settings = _settings()
    show_confidence = bool(settings.get("display_confidence", True))
    show_hint = bool(settings.get("llm_show_hint", True))
    min_confidence = float(settings.get("confidence_threshold", 0.0))
    position = settings.get("annotation_position", "eol")
    layout = sublime.LAYOUT_INLINE if position == "eol" else sublime.LAYOUT_BELOW
    llm_hide_non_refined = bool(settings.get("llm_hide_non_refined", True))
    llm_active = bool(settings.get("llm_enabled", True)) and _llm_refiner() is not None

    phantoms = []
    for line_no in sorted(state.analyses):
        analysis = state.analyses[line_no]
        if llm_active and llm_hide_non_refined and analysis.source != "llm":
            continue
        if analysis.confidence < min_confidence:
            continue
        region = _line_region(view, line_no)
        if region is None:
            continue

        html = render_analysis_html(
            analysis,
            show_confidence=show_confidence,
            show_hint=show_hint,
            inline_mode=(layout == sublime.LAYOUT_INLINE),
        )
        point = region.end() if layout == sublime.LAYOUT_INLINE else region.begin()
        anchor = sublime.Region(point, point)
        phantoms.append(sublime.Phantom(anchor, html, layout))

    state.phantom_set.update(phantoms)
    _refresh_stress_regions(view, state)
    _maybe_auto_dump_debug()


def _parse_hex_color(value: str) -> Optional[Tuple[int, int, int]]:
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 8:
        value = value[2:]
    if len(value) != 6:
        return None
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return None


def _luma(rgb: Tuple[int, int, int]) -> float:
    # Relative luminance (0..255-ish); good enough for dark/light heuristics.
    r, g, b = rgb
    return 0.2126 * float(r) + 0.7152 * float(g) + 0.0722 * float(b)


def _parse_bg_for_scope(view: sublime.View, scope: str) -> Optional[Tuple[int, int, int]]:
    try:
        style = view.style_for_scope(scope)
        bg = style.get("background") if isinstance(style, dict) else None
        if isinstance(bg, str):
            return _parse_hex_color(bg)
    except Exception:
        return None
    return None


def _pick_stress_scope(view: sublime.View) -> str:
    bg = _view_background_rgb(view)
    if bg is None:
        return "region.grayish"

    bg_l = _luma(bg)
    best_scope = ""
    best_dist = None
    # Pick the closest *darker* background among common region scopes.
    for scope in STRESS_SCOPE_CANDIDATES:
        rgb = _parse_bg_for_scope(view, scope)
        if rgb is None:
            continue
        if _luma(rgb) >= bg_l - 1.0:
            continue
        dist = abs(rgb[0] - bg[0]) + abs(rgb[1] - bg[1]) + abs(rgb[2] - bg[2])
        # If it's very close it may be subtle; still accept it.
        if dist < 4:
            continue
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_scope = scope

    return best_scope or "region.grayish"


def _recover_overlay_color_scheme(view: sublime.View) -> None:
    # Older versions of this package could leave a view stuck on a generated overlay scheme.
    # If that happens, force a return to "auto" (which will track Preferences dark/light).
    try:
        scheme = str(view.settings().get("color_scheme") or "").strip()
        if scheme.endswith("/" + OVERLAY_SCHEME_FILENAME) or scheme.endswith(OVERLAY_SCHEME_FILENAME):
            view.settings().set("color_scheme", "auto")
    except Exception:
        pass


def _view_background_rgb(view: sublime.View) -> Optional[Tuple[int, int, int]]:
    try:
        style = view.style()
        bg = style.get("background") if isinstance(style, dict) else None
        if isinstance(bg, str):
            parsed = _parse_hex_color(bg)
            if parsed is not None:
                return parsed
    except Exception:
        pass

    try:
        style = view.style_for_scope("text.plain")
        bg = style.get("background") if isinstance(style, dict) else None
        if isinstance(bg, str):
            return _parse_hex_color(bg)
    except Exception:
        pass

    return None


def _refresh_stress_regions(view: sublime.View, state: ViewState) -> None:
    settings = _settings()
    highlight_enabled = bool(settings.get("highlight_stress_words", True))
    if not highlight_enabled:
        view.erase_regions(STRESS_REGION_KEY)
        return

    llm_override_stress = bool(settings.get("llm_override_stress", True))
    llm_stress_only_when_refined = bool(settings.get("llm_stress_only_when_refined", True))
    llm_active = bool(settings.get("llm_enabled", True)) and _llm_refiner() is not None

    scope_setting = str(settings.get("stress_scope", "auto") or "auto").strip()
    if (not scope_setting) or scope_setting.lower() == "auto":
        scope = _pick_stress_scope(view)
    else:
        # Accept only region scopes. Older configs used custom scopes that many themes
        # don't define, which makes the fill effectively invisible.
        if not scope_setting.startswith("region."):
            scope = _pick_stress_scope(view)
        else:
            scope = scope_setting
    regions: List[sublime.Region] = []

    for line_no in sorted(state.analyses):
        analysis = state.analyses[line_no]
        if llm_active and llm_override_stress and llm_stress_only_when_refined and analysis.source != "llm":
            continue
        line_region = _line_region(view, line_no)
        if line_region is None:
            continue

        text = view.substr(line_region)
        matches = list(WORD_TOKEN_RE.finditer(text))
        token_patterns = analysis.token_patterns or []
        limit = min(len(matches), len(token_patterns))

        for idx in range(limit):
            pattern = token_patterns[idx]
            if not pattern:
                continue
            match = matches[idx]
            syllables = _syllable_spans(match.group(0), len(pattern))
            for syl_idx, stress_flag in enumerate(pattern):
                if stress_flag != "S":
                    continue
                if syl_idx >= len(syllables):
                    continue
                rel_start, rel_end = syllables[syl_idx]
                if rel_end <= rel_start:
                    continue
                start = line_region.begin() + match.start() + rel_start
                end = line_region.begin() + match.start() + rel_end
                regions.append(sublime.Region(start, end))

    flags = sublime.DRAW_NO_OUTLINE
    view.add_regions(STRESS_REGION_KEY, regions, scope, flags=flags)


def _background_rescan_tick() -> None:
    global _BACKGROUND_TICK_ACTIVE
    if not _BACKGROUND_TICK_ACTIVE:
        return

    settings = _settings()
    interval_ms = int(settings.get("background_rescan_interval_ms", 1000))
    if interval_ms > 0:
        for state in list(_VIEW_STATES.values()):
            if not state.enabled:
                continue

            view = state.view
            if view is None:
                continue
            if hasattr(view, "is_valid") and not view.is_valid():
                continue
            if not _is_supported_view(view):
                continue
            if view.is_loading():
                continue

            lines = _visible_line_numbers(view)
            if not lines:
                continue
            _schedule_scan(view, state, line_numbers=lines, debounce_override=0)

    next_delay = max(250, interval_ms if interval_ms > 0 else 1000)
    sublime.set_timeout_async(_background_rescan_tick, next_delay)


def _start_background_rescan_loop() -> None:
    global _BACKGROUND_TICK_ACTIVE
    if _BACKGROUND_TICK_ACTIVE:
        return
    _BACKGROUND_TICK_ACTIVE = True
    sublime.set_timeout_async(_background_rescan_tick, 250)


def _maybe_auto_dump_debug() -> None:
    global _DEBUG_LAST_BUNDLE_AT
    if not _debug_enabled():
        return
    settings = _settings()
    if not bool(settings.get("debug_auto_dump", False)):
        return
    interval_ms = int(settings.get("debug_auto_dump_interval_ms", 1000))
    if interval_ms <= 0:
        return
    now = time.time()
    if (now - _DEBUG_LAST_BUNDLE_AT) * 1000.0 < float(interval_ms):
        return
    _DEBUG_LAST_BUNDLE_AT = now
    _dump_debug_bundle(reason="auto")


def _debug_control_tick() -> None:
    global _DEBUG_CONTROL_LAST_MTIME
    if not _debug_enabled():
        sublime.set_timeout_async(_debug_control_tick, 750)
        return

    path = _debug_control_path()
    if not path:
        sublime.set_timeout_async(_debug_control_tick, 750)
        return

    try:
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            if mtime > _DEBUG_CONTROL_LAST_MTIME:
                _DEBUG_CONTROL_LAST_MTIME = mtime
                with open(path, "r", encoding="utf-8") as fh:
                    raw = fh.read()
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {}

                if isinstance(payload, dict):
                    # Use an explicit id if provided, else derive one from mtime.
                    cmd_id = payload.get("id")
                    if not isinstance(cmd_id, int):
                        cmd_id = int(mtime * 1000.0)
                    last_id = _read_control_state()
                    if int(cmd_id) <= int(last_id):
                        sublime.set_timeout_async(_debug_control_tick, 750)
                        return

                    do_dump = bool(payload.get("dump"))
                    do_reload = bool(payload.get("reload"))
                    do_rescan = bool(payload.get("rescan"))
                    do_clear = bool(payload.get("clear"))

                    # Clear one-shot flags first so a reload doesn't repeat the action forever.
                    if do_dump or do_reload or do_rescan or do_clear:
                        _write_control_state(int(cmd_id))
                        payload["dump"] = False
                        payload["reload"] = False
                        payload["rescan"] = False
                        payload["clear"] = False
                        payload["handled_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                        try:
                            with open(path, "w", encoding="utf-8") as fh:
                                json.dump(payload, fh, indent=2, sort_keys=True)
                            _DEBUG_CONTROL_LAST_MTIME = os.path.getmtime(path)
                        except Exception:
                            pass

                    if do_clear:
                        for state in list(_VIEW_STATES.values()):
                            if state.view is None:
                                continue
                            try:
                                _clear_annotations(state.view)
                            except Exception:
                                pass
                        _log_force("control: cleared annotations")

                    if do_rescan:
                        for state in list(_VIEW_STATES.values()):
                            if not state.enabled or state.view is None:
                                continue
                            view = state.view
                            try:
                                lines = _visible_line_numbers(view)
                                if lines:
                                    _schedule_scan(view, state, line_numbers=lines, debounce_override=0)
                            except Exception:
                                pass
                        _log_force("control: scheduled rescan (visible lines)")

                    if do_dump:
                        _dump_debug_bundle(reason="control")
                    if do_reload:
                        try:
                            sublime_plugin.reload_plugin("PoetryMeter.poetry_meter")
                            _log_force("control: reloaded PoetryMeter.poetry_meter")
                        except Exception as exc:
                            _log_force("control: reload failed: {}".format(repr(exc)))
    except Exception:
        pass

    sublime.set_timeout_async(_debug_control_tick, 750)


def _render_debug_view(view: sublime.View, state: ViewState) -> str:
    # Human-readable snapshot of what the plugin is trying to render.
    out: List[str] = []
    try:
        fname = view.file_name() or ""
    except Exception:
        fname = ""
    out.append("view_id={} file={!r}".format(view.id(), fname))

    for line_no in sorted(state.analyses):
        region = _line_region(view, line_no)
        if region is None:
            continue
        text = view.substr(region)
        analysis = state.analyses[line_no]

        label = "{} {:.0f}% src={}".format(analysis.meter_name, analysis.confidence * 100.0, analysis.source)
        out.append("{:>4d}: {} || {}".format(line_no + 1, text.rstrip("\n"), label))

        # Build a caret overlay for stressed syllables.
        marks = [" "] * len(text)
        matches = list(WORD_TOKEN_RE.finditer(text))
        token_patterns = analysis.token_patterns or []
        limit = min(len(matches), len(token_patterns))
        for idx in range(limit):
            pattern = token_patterns[idx]
            if not pattern:
                continue
            match = matches[idx]
            spans = _syllable_spans(match.group(0), len(pattern))
            for syl_idx, flag in enumerate(pattern):
                if flag != "S":
                    continue
                if syl_idx >= len(spans):
                    continue
                rs, re_ = spans[syl_idx]
                for j in range(match.start() + rs, min(match.start() + re_, len(marks))):
                    marks[j] = "^"
        if "^" in marks:
            out.append("      " + "".join(marks).rstrip())
    return "\n".join(out) + "\n"


def _dump_debug_bundle(reason: str = "manual") -> None:
    if not _debug_enabled():
        return

    settings = _settings()
    bundle_path = _debug_bundle_path()
    render_path = _debug_render_path()
    # When explicitly requested, also write a reason-specific snapshot so it doesn't get
    # immediately overwritten by the next auto-dump.
    bundle_path_reason = _path_with_reason(bundle_path, reason) if reason != "auto" else ""
    render_path_reason = _path_with_reason(render_path, reason) if reason != "auto" else ""

    data: Dict[str, object] = {
        "ts": time.time(),
        "reason": reason,
        "version": PLUGIN_VERSION,
        "plugin_file": __file__,
        "settings": {
            "enabled_file_extensions": settings.get("enabled_file_extensions", []),
            "opt_in_file_extensions": settings.get("opt_in_file_extensions", []),
            "opt_in_marker": settings.get("opt_in_marker", ""),
            "opt_out_marker": settings.get("opt_out_marker", ""),
            "marker_scan_max_lines": settings.get("marker_scan_max_lines", 0),
            "annotation_position": settings.get("annotation_position", "eol"),
            "highlight_stress_words": settings.get("highlight_stress_words", True),
            "stress_scope": settings.get("stress_scope", "auto"),
            "llm_enabled": settings.get("llm_enabled", True),
            "llm_endpoint": settings.get("llm_endpoint", ""),
            "llm_model": settings.get("llm_model", ""),
            "llm_timeout_ms": settings.get("llm_timeout_ms", 0),
            "llm_max_lines_per_scan": settings.get("llm_max_lines_per_scan", 0),
            "llm_sidecar_auto_start": settings.get("llm_sidecar_auto_start", False),
            "llm_sidecar_binary_path": settings.get("llm_sidecar_binary_path", ""),
            "llm_sidecar_port": settings.get("llm_sidecar_port", 0),
        },
        "views": [],
    }

    try:
        refiner = _llm_refiner()
        data["llm_last_error"] = refiner.last_error() if refiner is not None else ""
    except Exception:
        data["llm_last_error"] = "<error>"

    render_chunks: List[str] = []
    for view_id in sorted(_VIEW_STATES):
        state = _VIEW_STATES.get(view_id)
        if state is None or state.view is None:
            continue
        view = state.view
        try:
            fname = view.file_name() or ""
        except Exception:
            fname = ""
        try:
            supported = _is_supported_view(view)
        except Exception:
            supported = False

        try:
            total_lines = _line_count(view)
        except Exception:
            total_lines = 0

        try:
            allowed_typst = None
            if fname.lower().endswith(".typ"):
                allowed_typst = sorted(_typst_poetry_lines_upto(view, total_lines - 1))
        except Exception:
            allowed_typst = None

        view_obj: Dict[str, object] = {
            "view_id": view.id(),
            "file": fname,
            "supported": supported,
            "enabled": bool(state.enabled),
            "size": view.size(),
            "total_lines": total_lines,
            "typst_allowed_lines": allowed_typst,
            "analyses": [],
        }

        # Include a subset of analyses to keep the bundle small.
        for line_no in sorted(state.analyses)[:200]:
            analysis = state.analyses[line_no]
            try:
                region = _line_region(view, line_no)
                text = view.substr(region) if region is not None else ""
            except Exception:
                text = ""
            view_obj["analyses"].append(
                {
                    "line_no": int(line_no),
                    "text": text,
                    "meter_name": analysis.meter_name,
                    "confidence": analysis.confidence,
                    "source": analysis.source,
                    "stress_pattern": analysis.stress_pattern,
                    "token_patterns": analysis.token_patterns,
                    "analysis_hint": analysis.analysis_hint,
                }
            )

        data["views"].append(view_obj)
        try:
            render_chunks.append(_render_debug_view(view, state))
        except Exception:
            pass

    if bundle_path:
        try:
            parent = os.path.dirname(bundle_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(bundle_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
        except Exception as exc:
            _log_force("debug_bundle write failed: {}".format(repr(exc)))

    if bundle_path_reason:
        try:
            parent = os.path.dirname(bundle_path_reason)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(bundle_path_reason, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
        except Exception:
            pass

    if render_path:
        try:
            parent = os.path.dirname(render_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(render_path, "w", encoding="utf-8") as fh:
                fh.write("\n\n".join(render_chunks).strip() + "\n")
        except Exception as exc:
            _log_force("debug_render write failed: {}".format(repr(exc)))

    if render_path_reason:
        try:
            parent = os.path.dirname(render_path_reason)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(render_path_reason, "w", encoding="utf-8") as fh:
                fh.write("\n\n".join(render_chunks).strip() + "\n")
        except Exception:
            pass

    _log_throttled("dump_bundle", "debug bundle wrote reason={!r}".format(reason))


def _clear_annotations(view: sublime.View) -> None:
    state = _VIEW_STATES.get(view.id())
    if state is None:
        return
    state.analyses.clear()
    state.phantom_set.update([])
    view.erase_regions(STRESS_REGION_KEY)
    _recover_overlay_color_scheme(view)


def _apply_bootstrap_sidecar_settings(runtime_path: str, model_path: str) -> None:
    settings = _settings()
    settings.set("llm_enabled", True)
    settings.set("llm_sidecar_auto_start", True)
    settings.set("llm_sidecar_binary_path", runtime_path)
    settings.set("llm_sidecar_model_path", model_path)
    settings.set(
        "llm_sidecar_command_template",
        ["{binary_path}", "--model", "{model_path}", "--host", "{host}", "--port", "{port}"],
    )
    settings.set("llm_sidecar_host", "127.0.0.1")
    settings.set("llm_sidecar_port", 0)
    settings.set("llm_sidecar_healthcheck_path", "/v1/models")
    if not str(settings.get("llm_model", "") or "").strip():
        settings.set("llm_model", "local-model")
    sublime.save_settings(PACKAGE_SETTINGS)
    _reset_llm_runtime_state()


def _reset_llm_runtime_state() -> None:
    global _LLM_REFINER
    global _LLM_REFINER_CFG
    global _LLM_SIDECAR
    global _LLM_SIDECAR_CFG

    with _LLM_LOCK:
        _LLM_REFINER = None
        _LLM_REFINER_CFG = None

        if _LLM_SIDECAR is not None:
            _LLM_SIDECAR.stop()
        _LLM_SIDECAR = None
        _LLM_SIDECAR_CFG = None


def _configure_ollama_sidecar(runtime_path: str, model_name: str) -> None:
    settings = _settings()
    settings.set("llm_enabled", True)
    settings.set("llm_model", model_name)
    settings.set("llm_sidecar_auto_start", True)
    settings.set("llm_sidecar_binary_path", runtime_path)
    settings.set("llm_sidecar_model_path", "")
    settings.set("llm_sidecar_host", "127.0.0.1")
    settings.set("llm_sidecar_port", 11434)
    settings.set("llm_sidecar_healthcheck_path", "/api/tags")
    settings.set("llm_sidecar_command_template", ["{binary_path}", "serve"])
    settings.set("llm_endpoint", "http://127.0.0.1:11434/v1/chat/completions")
    sublime.save_settings(PACKAGE_SETTINGS)
    _reset_llm_runtime_state()


def _detect_local_runtime(prefer_ollama: bool = True) -> Tuple[str, str]:
    def resolve(candidates: List[str]) -> str:
        for name in candidates:
            if os.path.isabs(name):
                if os.path.isfile(name) and os.access(name, os.X_OK):
                    return name
                continue
            found = shutil.which(name)
            if found:
                return found
        return ""

    ollama = resolve(
        [
            "ollama",
            "/opt/homebrew/bin/ollama",
            "/usr/local/bin/ollama",
            "/Applications/Ollama.app/Contents/MacOS/ollama",
        ]
    )
    llama_server = resolve(
        [
            "llama-server",
            "/opt/homebrew/bin/llama-server",
            "/usr/local/bin/llama-server",
        ]
    )
    llamafile = resolve(
        [
            "llamafile",
            "/opt/homebrew/bin/llamafile",
            "/usr/local/bin/llamafile",
        ]
    )

    if prefer_ollama:
        if ollama:
            return ("ollama", ollama)
        if llama_server:
            return ("llama-server", llama_server)
        if llamafile:
            return ("llamafile", llamafile)
    else:
        if llama_server:
            return ("llama-server", llama_server)
        if llamafile:
            return ("llamafile", llamafile)
        if ollama:
            return ("ollama", ollama)

    return ("", "")


def _pull_ollama_model(runtime_path: str, model_name: str, timeout_ms: int) -> str:
    try:
        timeout_s = max(30.0, float(timeout_ms) / 1000.0)
        completed = subprocess.run(
            [runtime_path, "pull", model_name],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except Exception as exc:
        return str(exc)

    if completed.returncode == 0:
        return ""

    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    return stderr or stdout or "exit code {}".format(completed.returncode)


def _ollama_installed_models(host: str, port: int, timeout_s: float = 1.0) -> List[str]:
    url = "http://{}:{}/api/tags".format(host, int(port))
    try:
        req = urllib.request.Request(url=url, method="GET")
        with urllib.request.urlopen(req, timeout=max(0.2, float(timeout_s))) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(raw)
        models = payload.get("models", [])
        out: List[str] = []
        if isinstance(models, list):
            for item in models:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    out.append(name.strip())
        return sorted(set(out))
    except Exception:
        return []

def _maybe_switch_to_installed_ollama_model(settings: sublime.Settings, llm_refiner: LLMRefiner) -> None:
    # KISS: if the configured model doesn't exist, switch to an already-installed model.
    err = (llm_refiner.last_error() or "").lower()
    if "llm_http_error: 404" not in err or "not found" not in err or "model" not in err:
        return

    binary_path = str(settings.get("llm_sidecar_binary_path", "") or "").strip()
    if not binary_path or os.path.basename(binary_path) != "ollama":
        return

    host = str(settings.get("llm_sidecar_host", "127.0.0.1") or "127.0.0.1").strip()
    port = int(settings.get("llm_sidecar_port", 11434))
    installed = _ollama_installed_models(host, port, timeout_s=1.0)
    if not installed:
        sublime.status_message("Poetry Meter: Ollama has no models. Run: ollama pull qwen2.5:7b-instruct")
        return

    current = str(settings.get("llm_model", "") or "").strip()
    if current in installed:
        return

    preferred = ["qwen2.5:7b-instruct", "qwen2.5:3b-instruct", "llama3.1:8b", "llama3:8b"]
    chosen = ""
    for name in preferred:
        if name in installed:
            chosen = name
            break
    if not chosen:
        chosen = installed[0]

    settings.set("llm_model", chosen)
    sublime.save_settings(PACKAGE_SETTINGS)
    llm_refiner.clear_cache()
    _log("ollama: switched llm_model to installed {!r}".format(chosen))
    sublime.status_message("Poetry Meter: switched model to {}; rescanning...".format(chosen))

    for state in list(_VIEW_STATES.values()):
        if not state.enabled or state.view is None:
            continue
        view = state.view
        if hasattr(view, "is_valid") and not view.is_valid():
            continue
        if not _is_supported_view(view):
            continue
        lines = _visible_line_numbers(view)
        if lines:
            _schedule_scan(view, state, line_numbers=lines, debounce_override=0)

def plugin_loaded() -> None:
    _engine()
    _start_background_rescan_loop()
    try:
        path = __file__
    except Exception:
        path = "<unknown>"
    _log("plugin_loaded version={} file={!r}".format(PLUGIN_VERSION, path))
    sublime.set_timeout_async(_debug_control_tick, 750)
    _maybe_auto_dump_debug()


def plugin_unloaded() -> None:
    global _LLM_REFINER
    global _LLM_REFINER_CFG
    global _LLM_SIDECAR
    global _LLM_SIDECAR_CFG
    global _BACKGROUND_TICK_ACTIVE

    _BACKGROUND_TICK_ACTIVE = False
    for state in _VIEW_STATES.values():
        state.phantom_set.update([])
        if state.view is not None:
            _recover_overlay_color_scheme(state.view)
    _VIEW_STATES.clear()
    if _LLM_SIDECAR is not None:
        _LLM_SIDECAR.stop()
    _LLM_REFINER = None
    _LLM_REFINER_CFG = None
    _LLM_SIDECAR = None
    _LLM_SIDECAR_CFG = None


class PoetryMeterEventListener(sublime_plugin.EventListener):
    def on_load_async(self, view: sublime.View) -> None:
        if not _is_supported_view(view):
            return
        state = _ensure_state(view)
        _schedule_scan(view, state, force_full=True, debounce_override=0)

    def on_activated_async(self, view: sublime.View) -> None:
        if not _is_supported_view(view):
            return
        state = _ensure_state(view)
        if not state.analyses:
            _schedule_scan(view, state, force_full=True)

    def on_modified_async(self, view: sublime.View) -> None:
        if not _is_supported_view(view):
            return
        state = _ensure_state(view)
        if _line_count(view) != state.last_line_count:
            _schedule_scan(view, state, force_full=True)
        else:
            changed = _selected_line_numbers(view)
            _schedule_scan(view, state, line_numbers=changed)

    def on_close(self, view: sublime.View) -> None:
        state = _VIEW_STATES.pop(view.id(), None)
        if state is not None:
            state.phantom_set.update([])
            _recover_overlay_color_scheme(view)


class PoetryMeterToggleLiveCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        view = self.view
        if not _is_supported_view(view):
            return

        state = _ensure_state(view)
        state.enabled = not state.enabled
        view.settings().set(VIEW_ENABLED_KEY, state.enabled)

        if state.enabled:
            _schedule_scan(view, state, force_full=True, debounce_override=0)
            sublime.status_message("Poetry Meter: live annotations enabled")
        else:
            _clear_annotations(view)
            sublime.status_message("Poetry Meter: live annotations disabled")


class PoetryMeterOpenSettingsCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        self.window.run_command(
            "edit_settings",
            {
                "base_file": "Packages/PoetryMeter/PoetryMeter.sublime-settings",
                "user_file": "Packages/User/PoetryMeter.sublime-settings",
            },
        )


class PoetryMeterRestoreColorSchemeCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        _recover_overlay_color_scheme(self.view)
        sublime.status_message("Poetry Meter: restored color scheme (if needed)")


class PoetryMeterRescanBufferCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        view = self.view
        if not _is_supported_view(view):
            return

        state = _ensure_state(view)
        if not state.enabled:
            sublime.status_message("Poetry Meter: enable live annotations before rescanning")
            return

        _schedule_scan(view, state, force_full=True, debounce_override=0)
        sublime.status_message("Poetry Meter: rescanning buffer")


class PoetryMeterClearAnnotationsCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        _clear_annotations(self.view)
        sublime.status_message("Poetry Meter: annotations cleared")


class PoetryMeterClearLlmCacheCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        llm_refiner = _llm_refiner()
        if llm_refiner is None:
            sublime.status_message("Poetry Meter: LLM refinement is disabled")
            return
        llm_refiner.clear_cache()
        sublime.status_message("Poetry Meter: LLM cache cleared")


def _dump_debug_info(window: Optional[sublime.Window] = None, view: Optional[sublime.View] = None) -> None:
    settings = _settings()
    _log_force("=== dump_debug ===")
    try:
        _log_force("settings: llm_enabled={!r} endpoint={!r} model={!r} timeout_ms={!r} max_lines_per_scan={!r}".format(
            bool(settings.get("llm_enabled", True)),
            str(settings.get("llm_endpoint", "") or "").strip(),
            str(settings.get("llm_model", "") or "").strip(),
            int(settings.get("llm_timeout_ms", 0)),
            int(settings.get("llm_max_lines_per_scan", 0)),
        ))
        _log_force("settings: llm_sidecar_auto_start={!r} llm_sidecar_binary_path={!r} llm_sidecar_port={!r}".format(
            bool(settings.get("llm_sidecar_auto_start", False)),
            str(settings.get("llm_sidecar_binary_path", "") or "").strip(),
            int(settings.get("llm_sidecar_port", 0)),
        ))
        _log_force("settings: llm_override_meter={!r} llm_override_stress={!r} llm_stress_only_when_refined={!r} llm_hide_non_refined={!r}".format(
            bool(settings.get("llm_override_meter", True)),
            bool(settings.get("llm_override_stress", True)),
            bool(settings.get("llm_stress_only_when_refined", True)),
            bool(settings.get("llm_hide_non_refined", True)),
        ))
        _log_force("settings: highlight_stress_words={!r} stress_scope={!r} annotation_position={!r}".format(
            bool(settings.get("highlight_stress_words", True)),
            str(settings.get("stress_scope", "auto") or "auto"),
            str(settings.get("annotation_position", "eol") or "eol"),
        ))
        _log_force("settings: enabled_file_extensions={!r}".format(settings.get("enabled_file_extensions", [])))
        _log_force("settings: opt_in_file_extensions={!r} opt_in_marker={!r} opt_out_marker={!r} marker_scan_max_lines={!r}".format(
            settings.get("opt_in_file_extensions", []),
            str(settings.get("opt_in_marker", "") or "").strip(),
            str(settings.get("opt_out_marker", "") or "").strip(),
            int(settings.get("marker_scan_max_lines", 0)),
        ))
    except Exception:
        _log_force("settings: <error dumping settings>")

    try:
        refiner = _llm_refiner()
        if refiner is None:
            _log_force("llm_refiner: None")
        else:
            _log_force("llm_refiner: last_error={!r}".format(refiner.last_error()))
    except Exception:
        _log_force("llm_refiner: <error>")

    try:
        sidecar = _sidecar_manager()
        if sidecar is None:
            _log_force("sidecar: None")
        else:
            try:
                endpoint = sidecar.chat_completions_endpoint()
            except Exception:
                endpoint = None
            _log_force("sidecar: chat_endpoint={!r} last_error={!r}".format(endpoint, sidecar.last_error))
    except Exception:
        _log_force("sidecar: <error>")

    if view is None and window is not None:
        try:
            view = window.active_view()
        except Exception:
            view = None

    if view is not None:
        try:
            fname = view.file_name() or ""
            _log_force("view: id={} file={!r} size={} supported={!r} enabled={!r}".format(
                view.id(),
                fname,
                view.size(),
                _is_supported_view(view),
                bool(_VIEW_STATES.get(view.id()).enabled) if _VIEW_STATES.get(view.id()) else None,
            ))
            try:
                total_lines = _line_count(view)
                visible_lines = _visible_line_numbers(view)
                selected_lines = _selected_line_numbers(view)
                _log_force(
                    "view: total_lines={} visible_lines={} selected_lines={}".format(
                        total_lines,
                        len(visible_lines),
                        len(selected_lines),
                    )
                )
            except Exception:
                _log_force("view: <error dumping line ranges>")
            bg = _view_background_rgb(view)
            _log_force("view: background_rgb={!r} picked_stress_scope={!r}".format(bg, _pick_stress_scope(view)))
        except Exception:
            _log_force("view: <error dumping view>")

    try:
        if _VIEW_STATES:
            _log_force("tracked_views: n={}".format(len(_VIEW_STATES)))
            for sid in sorted(_VIEW_STATES):
                st = _VIEW_STATES.get(sid)
                v = st.view if st is not None else None
                if v is None:
                    _log_force("tracked_views: id={} <no view>".format(sid))
                    continue
                _log_force("tracked_views: id={} enabled={!r} file={!r}".format(
                    sid,
                    bool(st.enabled),
                    v.file_name() or "",
                ))
        else:
            _log_force("tracked_views: n=0")
    except Exception:
        _log_force("tracked_views: <error>")

    _log_force("=== /dump_debug ===")


class PoetryMeterEnableDebugLogsCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        settings = _settings()
        settings.set("debug_log", True)
        sublime.save_settings(PACKAGE_SETTINGS)
        _log_force("debug_log enabled")
        sublime.status_message("Poetry Meter: debug logs enabled")


class PoetryMeterDisableDebugLogsCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        settings = _settings()
        settings.set("debug_log", False)
        sublime.save_settings(PACKAGE_SETTINGS)
        _log_force("debug_log disabled")
        sublime.status_message("Poetry Meter: debug logs disabled")


class PoetryMeterDumpDebugCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        _dump_debug_info(window=self.window, view=None)
        sublime.status_message("Poetry Meter: debug info dumped (see /tmp/poetrymeter.log)")


class PoetryMeterDumpDebugBundleCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        _dump_debug_bundle(reason="manual")
        sublime.status_message("Poetry Meter: debug bundle dumped (see /tmp/poetrymeter_bundle.json)")


class PoetryMeterSetupLocalLlmCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        self._hardware = hardware_options()
        items: List[List[str]] = []
        for option in self._hardware:
            items.append([option["label"], option["description"]])
        self.window.show_quick_panel(items, self._on_hardware_selected)

    def _on_hardware_selected(self, index: int) -> None:
        if index < 0:
            return

        hardware_key = self._hardware[index]["key"]
        self._profiles = model_profiles_for_hardware(hardware_key)
        items: List[List[str]] = []
        for profile in self._profiles:
            items.append([profile.label, profile.description])
        self.window.show_quick_panel(items, self._on_profile_selected)

    def _on_profile_selected(self, index: int) -> None:
        if index < 0:
            return
        profile = self._profiles[index]
        sublime.status_message("Poetry Meter: configuring local LLM...")
        sublime.set_timeout_async(lambda: self._run_setup_async(profile), 0)

    def _run_setup_async(self, profile: SetupModelProfile) -> None:
        settings = _settings()
        prefer_ollama = bool(settings.get("llm_setup_prefer_ollama", True))
        runtime_kind, runtime_path = _detect_local_runtime(prefer_ollama=prefer_ollama)

        if runtime_kind == "ollama":
            _configure_ollama_sidecar(runtime_path, profile.ollama_model)

            pull_error = ""
            if bool(settings.get("llm_setup_auto_pull_ollama_model", True)):
                pull_timeout = int(settings.get("llm_setup_ollama_pull_timeout_ms", 900000))
                pull_error = _pull_ollama_model(runtime_path, profile.ollama_model, pull_timeout)

            sidecar = _sidecar_manager()
            endpoint = sidecar.restart() if sidecar is not None else None
            if endpoint and not pull_error:
                msg = "Poetry Meter: setup complete via Ollama ({})".format(profile.ollama_model)
                sublime.set_timeout(lambda: sublime.status_message(msg), 0)
                return

            if endpoint and pull_error:
                msg = (
                    "Poetry Meter: Ollama configured, but model pull reported an issue:\n\n{}"
                    "\n\nYou can still proceed if the model already exists."
                ).format(pull_error)
                sublime.set_timeout(lambda: sublime.error_message(msg), 0)
                return

            err = sidecar.last_error if sidecar is not None else "unknown startup failure"
            msg = "Poetry Meter: Ollama configured, but sidecar failed to start:\n\n{}".format(err)
            sublime.set_timeout(lambda: sublime.error_message(msg), 0)
            return

        if profile.gguf_url and not str(settings.get("llm_bootstrap_model_url", "") or "").strip():
            settings.set("llm_bootstrap_model_url", profile.gguf_url)
        if profile.gguf_sha256 and not str(settings.get("llm_bootstrap_model_sha256", "") or "").strip():
            settings.set("llm_bootstrap_model_sha256", profile.gguf_sha256)
        if profile.gguf_filename and not str(settings.get("llm_bootstrap_model_filename", "") or "").strip():
            settings.set("llm_bootstrap_model_filename", profile.gguf_filename)
        if runtime_path:
            settings.set("llm_sidecar_binary_path", runtime_path)
        sublime.save_settings(PACKAGE_SETTINGS)

        try:
            result = bootstrap_local_llm(settings)
            _apply_bootstrap_sidecar_settings(result.runtime_path, result.model_path)
        except BootstrapError as exc:
            guidance = ""
            if not runtime_path:
                guidance = (
                    "\n\nNo local runtime was detected.\n"
                    "Install Ollama and rerun setup, or configure bootstrap download URLs in settings."
                )
            msg = "Poetry Meter setup failed:\n\n{}{}".format(exc, guidance)
            sublime.set_timeout(lambda: sublime.error_message(msg), 0)
            return
        except Exception as exc:
            msg = "Poetry Meter setup failed unexpectedly:\n\n{}".format(exc)
            sublime.set_timeout(lambda: sublime.error_message(msg), 0)
            return

        sidecar = _sidecar_manager()
        endpoint = sidecar.restart() if sidecar is not None else None
        if endpoint:
            msg = "Poetry Meter: setup complete at {}".format(endpoint)
        else:
            err = sidecar.last_error if sidecar is not None else "unknown startup failure"
            msg = "Poetry Meter: setup completed, but sidecar start failed ({})".format(err)
        sublime.set_timeout(lambda: sublime.status_message(msg), 0)


class PoetryMeterBootstrapLocalLlmCommand(sublime_plugin.WindowCommand):
    def run(self) -> None:
        sublime.status_message("Poetry Meter: bootstrapping local LLM...")
        sublime.set_timeout_async(self._run_async, 0)

    def _run_async(self) -> None:
        try:
            result = bootstrap_local_llm(_settings())
            _apply_bootstrap_sidecar_settings(result.runtime_path, result.model_path)
        except BootstrapError as exc:
            message = "Poetry Meter bootstrap failed:\n\n{}".format(exc)
            sublime.set_timeout(lambda: sublime.error_message(message), 0)
            return
        except Exception as exc:
            message = "Poetry Meter bootstrap encountered an unexpected error:\n\n{}".format(exc)
            sublime.set_timeout(lambda: sublime.error_message(message), 0)
            return

        sidecar = _sidecar_manager()
        endpoint = sidecar.restart() if sidecar is not None else None
        if endpoint:
            msg = "Poetry Meter: local LLM ready at {}".format(endpoint)
        else:
            err = sidecar.last_error if sidecar is not None else ""
            if err:
                msg = "Poetry Meter: bootstrapped files, but sidecar start failed ({})".format(err)
            else:
                msg = "Poetry Meter: local LLM files bootstrapped"
        sublime.set_timeout(lambda: sublime.status_message(msg), 0)


class PoetryMeterRestartLlmSidecarCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        sidecar = _sidecar_manager()
        if sidecar is None:
            sublime.status_message("Poetry Meter: sidecar auto-start is disabled")
            return

        endpoint = sidecar.restart()
        if endpoint:
            sublime.status_message("Poetry Meter: sidecar running at {}".format(endpoint))
            return

        error = sidecar.last_error or "unknown start failure"
        sublime.status_message("Poetry Meter: sidecar restart failed ({})".format(error))


class PoetryMeterStopLlmSidecarCommand(sublime_plugin.TextCommand):
    def run(self, edit: sublime.Edit) -> None:
        sidecar = _sidecar_manager()
        if sidecar is None:
            sublime.status_message("Poetry Meter: sidecar auto-start is disabled")
            return
        sidecar.stop()
        sublime.status_message("Poetry Meter: sidecar stopped")
