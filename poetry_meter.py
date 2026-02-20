import os
import shutil
import subprocess
import threading
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

_VIEW_STATES: Dict[int, "ViewState"] = {}
_ENGINE: Optional[MeterEngine] = None
_ENGINE_DICT_PATH: Optional[str] = None
_LLM_REFINER: Optional[LLMRefiner] = None
_LLM_REFINER_CFG: Optional[Tuple[str, str, str, str, int]] = None
_LLM_SIDECAR: Optional[LLMSidecarManager] = None
_LLM_SIDECAR_CFG: Optional[SidecarConfig] = None
_ENGINE_LOCK = threading.Lock()
_LLM_LOCK = threading.Lock()


class ViewState:
    def __init__(self, view: sublime.View, enabled: bool):
        self.view_id = view.id()
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
        return ""
    return str(settings.get("llm_endpoint", "") or "").strip()


def _llm_refiner() -> Optional[LLMRefiner]:
    global _LLM_REFINER
    global _LLM_REFINER_CFG

    settings = _settings()
    if not bool(settings.get("llm_enabled", True)):
        return None

    endpoint = _resolve_llm_endpoint()
    model = str(settings.get("llm_model", "") or "").strip()
    api_key = str(settings.get("llm_api_key", "") or "").strip()
    prompt_version = str(settings.get("llm_prompt_version", "v1") or "v1").strip()
    error_cooldown_ms = int(settings.get("llm_error_cooldown_ms", 3000))

    if not endpoint or not model:
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


def _is_supported_view(view: sublime.View) -> bool:
    if view is None:
        return False
    if view.settings().get("is_widget"):
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
) -> LineAnalysis:
    stress_pattern = analysis.stress_pattern
    meter_name = analysis.meter_name
    confidence = analysis.confidence
    feet_count = analysis.feet_count

    if override_meter:
        stress_pattern = llm_refinement.stress_pattern
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
        analysis_hint=llm_refinement.analysis_hint,
        source="llm",
    )


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
    max_line_length = int(settings.get("max_line_length", 220))
    llm_refiner = _llm_refiner()
    llm_timeout_ms = int(settings.get("llm_timeout_ms", 1000))
    llm_temperature = float(settings.get("llm_temperature", 0.1))
    llm_max_lines = max(0, int(settings.get("llm_max_lines_per_scan", 1)))
    llm_override_meter = bool(settings.get("llm_override_meter", True))
    llm_remaining = llm_max_lines

    if force_full:
        target_lines = _resolve_full_scan_lines(view, settings)
    else:
        target_lines = line_numbers or _selected_line_numbers(view)

    if not target_lines:
        return

    target_lines = set(target_lines)
    analyses = dict(state.analyses)
    meter = _engine()

    for line_no in sorted(target_lines):
        region = _line_region(view, line_no)
        if region is None:
            analyses.pop(line_no, None)
            continue

        raw_line = view.substr(region)
        if not raw_line.strip() or len(raw_line) > max_line_length:
            analyses.pop(line_no, None)
            continue

        analysis = meter.analyze_line(raw_line, line_no=line_no)
        if analysis is None:
            analyses.pop(line_no, None)
        else:
            if llm_refiner is not None and llm_remaining > 0:
                refinement = llm_refiner.refine_line(
                    line_text=raw_line,
                    baseline=analysis,
                    timeout_ms=llm_timeout_ms,
                    temperature=llm_temperature,
                )
                llm_remaining -= 1
                if refinement is not None:
                    analysis = _merge_llm_refinement(
                        analysis=analysis,
                        llm_refinement=refinement,
                        override_meter=llm_override_meter,
                    )
            analyses[line_no] = analysis

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

    phantoms = []
    for line_no in sorted(state.analyses):
        analysis = state.analyses[line_no]
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


def _clear_annotations(view: sublime.View) -> None:
    state = _VIEW_STATES.get(view.id())
    if state is None:
        return
    state.analyses.clear()
    state.phantom_set.update([])


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


def plugin_loaded() -> None:
    _engine()


def plugin_unloaded() -> None:
    global _LLM_REFINER
    global _LLM_REFINER_CFG
    global _LLM_SIDECAR
    global _LLM_SIDECAR_CFG

    for state in _VIEW_STATES.values():
        state.phantom_set.update([])
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
