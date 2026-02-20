import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Dict, Optional

try:
    from .meter_engine import LineAnalysis
except ImportError:
    from meter_engine import LineAnalysis

METER_RE = re.compile(
    r"^(iambic|trochaic|anapestic|dactylic)\s+"
    r"(monometer|dimeter|trimeter|tetrameter|pentameter|hexameter)$"
)
STRESS_RE = re.compile(r"^[US]+$")

TransportFn = Callable[[str, Dict[str, str], str, float], str]


@dataclass
class LLMRefinement:
    stress_pattern: str
    meter_name: str
    confidence: float
    analysis_hint: str


class LLMRefiner:
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str = "",
        prompt_version: str = "v1",
        error_cooldown_ms: int = 3000,
        transport: Optional[TransportFn] = None,
    ) -> None:
        self._endpoint = endpoint
        self._model = model
        self._api_key = api_key
        self._prompt_version = prompt_version
        self._error_cooldown_ms = max(0, int(error_cooldown_ms))
        self._transport = transport or self._default_transport
        self._cache: Dict[str, Optional[LLMRefinement]] = {}
        self._lock = threading.Lock()
        self._cooldown_until = 0.0

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cooldown_until = 0.0

    def _cache_key(self, line_text: str) -> str:
        material = "\n".join((self._prompt_version, self._model, line_text.strip()))
        return sha256(material.encode("utf-8")).hexdigest()

    def refine_line(
        self,
        line_text: str,
        baseline: LineAnalysis,
        timeout_ms: int,
        temperature: float,
    ) -> Optional[LLMRefinement]:
        cache_key = self._cache_key(line_text)

        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
            if time.time() < self._cooldown_until:
                return None

        payload = self._build_payload(line_text, baseline, temperature)

        try:
            timeout_s = max(0.05, float(timeout_ms) / 1000.0)
            body = self._transport(self._endpoint, self._headers(), json.dumps(payload), timeout_s)
            refinement = self._parse_response(body)
        except Exception:
            refinement = None

        with self._lock:
            self._cache[cache_key] = refinement
            if refinement is None and self._error_cooldown_ms > 0:
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
        top_candidates = []
        for label, score in baseline.debug_scores.items():
            meter_label = label.replace(":", " ", 1)
            top_candidates.append({"meter": meter_label, "score": round(float(score), 4)})

        system_prompt = (
            "You are an expert in English poetic meter. "
            "Return ONLY JSON with keys: final_stress_pattern, final_meter, meter_confidence, analysis_hint. "
            "final_stress_pattern must be only U/S characters. "
            "final_meter must be one of iambic|trochaic|anapestic|dactylic plus a line-length label "
            "(monometer|dimeter|trimeter|tetrameter|pentameter|hexameter). "
            "meter_confidence must be a 0..1 float. "
            "analysis_hint must be <= 220 chars and practically useful to a poet."
        )

        user_payload = {
            "line_text": line_text,
            "tokens": baseline.tokens,
            "baseline": {
                "stress_pattern": baseline.stress_pattern,
                "meter_name": baseline.meter_name,
                "confidence": baseline.confidence,
                "oov_tokens": baseline.oov_tokens,
                "top_candidates": top_candidates[:6],
            },
            "task": "Improve meter classification accuracy and provide one concise craft hint.",
        }

        return {
            "model": self._model,
            "temperature": max(0.0, min(1.0, float(temperature))),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=True)},
            ],
        }

    def _parse_response(self, raw: str) -> Optional[LLMRefinement]:
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

        if not isinstance(stress, str) or not STRESS_RE.match(stress):
            return None
        if len(stress) < 1 or len(stress) > 80:
            return None

        if not isinstance(meter, str) or not METER_RE.match(meter.strip().lower()):
            return None

        if not isinstance(confidence, (int, float)):
            return None

        if not isinstance(hint, str):
            return None

        normalized_meter = meter.strip().lower()
        normalized_conf = max(0.0, min(1.0, float(confidence)))
        normalized_hint = " ".join(hint.split())[:220]

        return LLMRefinement(
            stress_pattern=stress,
            meter_name=normalized_meter,
            confidence=normalized_conf,
            analysis_hint=normalized_hint,
        )

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
