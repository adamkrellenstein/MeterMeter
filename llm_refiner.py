import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Dict, List, Optional

try:
    from .meter_engine import LineAnalysis
except ImportError:
    from meter_engine import LineAnalysis

METER_RE = re.compile(
    r"^(iambic|trochaic|anapestic|dactylic)\s+"
    r"(monometer|dimeter|trimeter|tetrameter|pentameter|hexameter)$"
)
STRESS_RE = re.compile(r"^[US]+$")
FOOT_TEMPLATES = {
    "iambic": "US",
    "trochaic": "SU",
    "anapestic": "UUS",
    "dactylic": "SUU",
}
LINE_NAME_TO_FEET = {
    "monometer": 1,
    "dimeter": 2,
    "trimeter": 3,
    "tetrameter": 4,
    "pentameter": 5,
    "hexameter": 6,
}

TransportFn = Callable[[str, Dict[str, str], str, float], str]
LogFn = Callable[[str], None]


@dataclass
class LLMRefinement:
    stress_pattern: str
    meter_name: str
    confidence: float
    analysis_hint: str
    token_patterns: List[str]


class LLMRefiner:
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str = "",
        prompt_version: str = "v2",
        error_cooldown_ms: int = 3000,
        transport: Optional[TransportFn] = None,
        logger: Optional[LogFn] = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._prompt_version = prompt_version
        self._error_cooldown_ms = max(0, int(error_cooldown_ms))
        self._transport = transport or self._default_transport
        self._logger = logger or (lambda _m: None)
        self._cache: Dict[str, Optional[LLMRefinement]] = {}
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self._last_error: str = ""
        self._last_error_at: float = 0.0

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cooldown_until = 0.0
            self._last_error = ""
            self._last_error_at = 0.0

    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def _set_last_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = (msg or "").strip()
            self._last_error_at = time.time()

    def _cache_key(self, line_text: str) -> str:
        material = "\n".join((self._prompt_version, self._model, line_text.strip()))
        return sha256(material.encode("utf-8")).hexdigest()

    def refine_lines(
        self,
        baselines: List[LineAnalysis],
        timeout_ms: int,
        temperature: float,
    ) -> Dict[int, LLMRefinement]:
        """
        Batch refine multiple lines in a single LLM request.

        Returns mapping line_no -> refinement for successful items. Uses per-line cache.
        """
        if not baselines:
            return {}

        pending: List[LineAnalysis] = []
        with self._lock:
            if time.time() < self._cooldown_until:
                self._logger("batch: in cooldown, skipping")
                return {}
            for baseline in baselines:
                cache_key = self._cache_key(baseline.source_text)
                cached = self._cache.get(cache_key)
                if cached is not None:
                    continue
                pending.append(baseline)

        if not pending:
            self._logger("batch: all cache hits (n={})".format(len(baselines)))
            out: Dict[int, LLMRefinement] = {}
            with self._lock:
                for baseline in baselines:
                    cached = self._cache.get(self._cache_key(baseline.source_text))
                    if cached is not None:
                        out[baseline.line_no] = cached
            return out

        self._logger("batch: pending={} requested={}".format(len(pending), len(baselines)))
        refinements: Dict[int, LLMRefinement] = {}
        body = ""
        had_timeout = False

        # Keep prompts small to avoid timeouts on cold starts or slower models.
        chunk_size = 3
        timeout_s = max(0.05, float(timeout_ms) / 1000.0)
        for start in range(0, len(pending), chunk_size):
            chunk = pending[start : start + chunk_size]
            payload = self._build_batch_payload(chunk, temperature)
            try:
                body = self._transport(self._endpoint, self._headers(), json.dumps(payload), timeout_s)
                self._logger("batch: response_bytes={}".format(len(body or "")))
                parsed = self._parse_batch_response(body, {b.line_no: b for b in chunk})
                refinements.update(parsed)
            except Exception as exc:
                had_timeout = had_timeout or isinstance(exc, TimeoutError) or ("timed out" in str(exc).lower())
                self._logger("batch: exception: {}".format(repr(exc)))
                self._set_last_error(repr(exc))

        if not refinements:
            try:
                snippet = (body or "").strip().replace("\n", " ")[:220]
                if snippet:
                    self._logger("batch: response_snippet={!r}".format(snippet))
            except Exception:
                pass
            # Some OpenAI-compatible servers/models ignore batch formatting. Fall back to
            # single-line refinement so the user still gets LLM-driven stress.
            self._logger("batch: no refinements parsed; falling back to single-line (max 8)")
            for baseline in pending[: min(8, len(pending))]:
                try:
                    one = self.refine_line(
                        line_text=baseline.source_text,
                        baseline=baseline,
                        timeout_ms=timeout_ms,
                        temperature=temperature,
                    )
                except Exception:
                    one = None
                if one is not None:
                    refinements[baseline.line_no] = one

        with self._lock:
            for baseline in pending:
                refinement = refinements.get(baseline.line_no)
                if refinement is not None:
                    self._cache[self._cache_key(baseline.source_text)] = refinement
            if (not had_timeout) and (not refinements) and self._error_cooldown_ms > 0:
                self._cooldown_until = time.time() + (self._error_cooldown_ms / 1000.0)

            out: Dict[int, LLMRefinement] = {}
            for baseline in baselines:
                cached = self._cache.get(self._cache_key(baseline.source_text))
                if cached is not None:
                    out[baseline.line_no] = cached
            self._logger("batch: returning refinements={}".format(len(out)))
            return out

    def refine_line(
        self,
        line_text: str,
        baseline: LineAnalysis,
        timeout_ms: int,
        temperature: float,
    ) -> Optional[LLMRefinement]:
        cache_key = self._cache_key(line_text)

        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            if time.time() < self._cooldown_until:
                return None

        payload = self._build_payload(line_text, baseline, temperature)

        had_timeout = False
        try:
            timeout_s = max(0.05, float(timeout_ms) / 1000.0)
            body = self._transport(self._endpoint, self._headers(), json.dumps(payload), timeout_s)
            self._logger("line: response_bytes={}".format(len(body or "")))
            refinement = self._parse_response(body, baseline)
        except Exception as exc:
            had_timeout = isinstance(exc, TimeoutError) or ("timed out" in str(exc).lower())
            self._set_last_error(repr(exc))
            self._logger("line: exception: {}".format(repr(exc)))
            refinement = None

        with self._lock:
            if refinement is not None:
                self._cache[cache_key] = refinement
            if (not had_timeout) and refinement is None and self._error_cooldown_ms > 0:
                self._cooldown_until = time.time() + (self._error_cooldown_ms / 1000.0)

        return refinement

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        return headers

    def _build_payload(self, line_text: str, baseline: LineAnalysis, temperature: float) -> Dict[str, object]:
        system_prompt = self._system_prompt()
        token_syllables: List[int] = []
        if isinstance(baseline.token_patterns, list) and baseline.token_patterns:
            for p in baseline.token_patterns:
                token_syllables.append(len(p) if isinstance(p, str) else 1)

        user_payload = {
            "line_text": line_text,
            "tokens": baseline.tokens,
            "token_syllables": token_syllables,
            "baseline": {
                "stress_pattern": baseline.stress_pattern,
                "meter_name": baseline.meter_name,
            },
            "task": self._task_prompt(),
        }

        return {
            "model": self._model,
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
        }

    def _build_batch_payload(self, baselines: List[LineAnalysis], temperature: float) -> Dict[str, object]:
        system_prompt = self._system_prompt_batch()
        lines: List[Dict[str, object]] = []

        for baseline in baselines:
            token_syllables: List[int] = []
            if isinstance(baseline.token_patterns, list) and baseline.token_patterns:
                for p in baseline.token_patterns:
                    token_syllables.append(len(p) if isinstance(p, str) else 1)

            lines.append(
                {
                    "line_no": baseline.line_no,
                    "line_text": baseline.source_text,
                    "tokens": baseline.tokens,
                    "token_syllables": token_syllables,
                    "baseline": {
                        "stress_pattern": baseline.stress_pattern,
                        "meter_name": baseline.meter_name,
                    },
                }
            )

        user_payload = {
            "task": self._task_prompt(),
            "lines": lines,
        }

        return {
            "model": self._model,
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": 1200,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
        }

    def _system_prompt_batch(self) -> str:
        return (
            "You are an expert in English poetic meter and scansion. "
            "Analyze stress at the line level, not as isolated dictionary accents per word. "
            "Prefer globally coherent rhythm over local lexical defaults, allowing common substitutions when plausible "
            "(initial inversion, occasional pyrrhic/spondaic feel, feminine ending). "
            "You will receive multiple lines at once. "
            "Return ONLY a single JSON object with key: results. "
            "results must be a list of objects, one per input line, each with keys: "
            "line_no, final_stress_pattern, final_meter, meter_confidence, analysis_hint, token_stress_patterns. "
            "Use only U/S for stress strings. "
            "token_stress_patterns must contain exactly one U/S string per input token, and concatenating them must produce final_stress_pattern. "
            "If token_syllables is provided, token_stress_patterns[i] length must equal token_syllables[i]. "
            "Each token_stress_patterns[i] marks stress per syllable of tokens[i] (length = syllable count). "
            "Example: tokens[i]=\"pregnant\" -> token_stress_patterns[i]=\"SU\". "
            "final_meter must be one of iambic|trochaic|anapestic|dactylic plus "
            "monometer|dimeter|trimeter|tetrameter|pentameter|hexameter. "
            "meter_confidence must be 0..1. "
            "analysis_hint must be <= 220 chars, concrete, and craft-useful."
        )

    def _system_prompt(self) -> str:
        if self._prompt_version == "v1":
            return (
                "You are an expert in English poetic meter. "
                "Return ONLY JSON with keys: final_stress_pattern, final_meter, meter_confidence, analysis_hint, token_stress_patterns. "
                "final_stress_pattern must be only U/S characters. "
                "final_meter must be one of iambic|trochaic|anapestic|dactylic plus a line-length label "
                "(monometer|dimeter|trimeter|tetrameter|pentameter|hexameter). "
                "meter_confidence must be a 0..1 float. "
                "analysis_hint must be <= 220 chars and practically useful to a poet. "
                "token_stress_patterns must be a list of U/S strings, one per token in order. "
                "Each token_stress_patterns[i] marks stress per syllable of tokens[i] (length = syllable count). "
                "Example: tokens[i]=\"pregnant\" -> token_stress_patterns[i]=\"SU\"."
            )

        return (
            "You are an expert in English poetic meter and scansion. "
            "Analyze stress at the line level, not as isolated dictionary accents per word. "
            "Prefer globally coherent rhythm over local lexical defaults, allowing common substitutions when plausible "
            "(initial inversion, occasional pyrrhic/spondaic feel, feminine ending). "
            "Return ONLY JSON with keys: final_stress_pattern, final_meter, meter_confidence, analysis_hint, token_stress_patterns. "
            "Use only U/S for stress strings. "
            "token_stress_patterns must contain exactly one U/S string per input token, and concatenating them must produce final_stress_pattern. "
            "If token_syllables is provided, token_stress_patterns[i] length must equal token_syllables[i]. "
            "Each token_stress_patterns[i] marks stress per syllable of tokens[i] (length = syllable count). "
            "Example: tokens[i]=\"pregnant\" -> token_stress_patterns[i]=\"SU\". "
            "final_meter must be one of iambic|trochaic|anapestic|dactylic plus "
            "monometer|dimeter|trimeter|tetrameter|pentameter|hexameter. "
            "meter_confidence must be 0..1. "
            "analysis_hint must be <= 220 chars, concrete, and craft-useful."
        )

    def _task_prompt(self) -> str:
        if self._prompt_version == "v1":
            return "Improve meter classification accuracy and provide one concise craft hint."
        return (
            "Choose a single best whole-line scansion. Re-weight stress contextually across the line, "
            "then provide one concise craft hint."
        )

    def _candidate_templates(self, candidates: List[Dict[str, object]]) -> List[Dict[str, object]]:
        out: List[Dict[str, object]] = []
        for candidate in candidates:
            meter_name = candidate.get("meter")
            if not isinstance(meter_name, str):
                continue
            template = self._meter_template(meter_name)
            if not template:
                continue
            out.append(
                {
                    "meter": meter_name,
                    "template": template,
                    "score": candidate.get("score", 0.0),
                }
            )
        return out

    def _meter_template(self, meter_name: str) -> str:
        parts = meter_name.strip().lower().split()
        if len(parts) != 2:
            return ""
        foot = FOOT_TEMPLATES.get(parts[0])
        feet = LINE_NAME_TO_FEET.get(parts[1])
        if not foot or not feet:
            return ""
        return foot * feet

    def _parse_response(self, raw: str, baseline: LineAnalysis) -> Optional[LLMRefinement]:
        data = json.loads(raw)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            return None

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return None

        payload = self._extract_json_payload(content)
        if payload is None:
            return None

        stress = payload.get("final_stress_pattern")
        meter = payload.get("final_meter")
        confidence = payload.get("meter_confidence")
        hint = payload.get("analysis_hint")
        token_patterns = payload.get("token_stress_patterns")

        if not isinstance(meter, str) or not METER_RE.match(meter.strip().lower()):
            return None

        if not isinstance(confidence, (int, float)):
            return None

        if not isinstance(hint, str):
            return None

        normalized_meter = meter.strip().lower()
        normalized_conf = max(0.0, min(1.0, float(confidence)))
        normalized_hint = " ".join(hint.split())[:220]
        normalized_token_patterns = self._normalize_token_patterns(
            token_patterns=token_patterns,
            token_count=len(baseline.tokens),
        )
        normalized_stress = ""
        if normalized_token_patterns:
            normalized_stress = "".join(normalized_token_patterns)

        if not normalized_stress:
            if not isinstance(stress, str):
                return None
            stress = stress.strip().upper()
            if not STRESS_RE.match(stress):
                return None
            normalized_stress = stress

        if len(normalized_stress) < 1 or len(normalized_stress) > 80:
            return None

        if not normalized_token_patterns:
            normalized_token_patterns = self._split_stress_by_baseline(normalized_stress, baseline.token_patterns)

        return LLMRefinement(
            stress_pattern=normalized_stress,
            meter_name=normalized_meter,
            confidence=normalized_conf,
            analysis_hint=normalized_hint,
            token_patterns=normalized_token_patterns,
        )

    def _parse_batch_response(self, raw: str, baselines: Dict[int, LineAnalysis]) -> Dict[int, LLMRefinement]:
        data = json.loads(raw)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return {}

        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            return {}

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            return {}

        payload = self._extract_json_payload(content)
        if payload is None:
            return {}

        results = payload.get("results")
        if not isinstance(results, list) or not results:
            return {}

        out: Dict[int, LLMRefinement] = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            line_no = item.get("line_no")
            if isinstance(line_no, str):
                try:
                    line_no = int(line_no.strip())
                except Exception:
                    line_no = None
            if not isinstance(line_no, int):
                continue
            baseline = baselines.get(line_no)
            if baseline is None:
                continue
            # Reuse the single-line parser/validator on a synthetic response.
            try:
                wrapped = {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(item, ensure_ascii=True),
                            }
                        }
                    ]
                }
                refinement = self._parse_response(json.dumps(wrapped), baseline)
            except Exception:
                refinement = None
            if refinement is not None:
                out[line_no] = refinement
        return out

    def _normalize_token_patterns(self, token_patterns, token_count: int) -> List[str]:
        if not isinstance(token_patterns, list) or not token_patterns:
            return []
        out: List[str] = []
        for item in token_patterns:
            if not isinstance(item, str):
                return []
            value = item.strip().upper()
            if not value or not STRESS_RE.match(value):
                return []
            out.append(value)
        if token_count > 0 and len(out) != token_count:
            return []
        return out

    def _split_stress_by_baseline(self, stress_pattern: str, baseline_token_patterns) -> List[str]:
        if not isinstance(baseline_token_patterns, list) or not baseline_token_patterns:
            return []
        out: List[str] = []
        idx = 0
        for item in baseline_token_patterns:
            if not isinstance(item, str) or not item:
                return []
            width = len(item)
            if idx + width > len(stress_pattern):
                return []
            out.append(stress_pattern[idx : idx + width])
            idx += width
        if idx != len(stress_pattern):
            return []
        return out

    def _extract_json_payload(self, content: str) -> Optional[Dict[str, object]]:
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            content = content.replace("json\n", "", 1).strip()

        direct = self._safe_json_obj(content)
        if direct is not None:
            return direct

        start = content.find("{")
        if start < 0:
            return None

        depth = 0
        for idx, ch in enumerate(content[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = content[start : idx + 1]
                    return self._safe_json_obj(candidate)
        return None

    def _safe_json_obj(self, text: str) -> Optional[Dict[str, object]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _default_transport(self, url: str, headers: Dict[str, str], body: str, timeout_s: float) -> str:
        req = urllib.request.Request(url=url, data=body.encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError("llm_http_error: {} {}".format(exc.code, detail))
