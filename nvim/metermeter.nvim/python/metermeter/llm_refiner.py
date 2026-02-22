import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from .meter_engine import LineAnalysis

WORD_RE = re.compile(r"^[A-Za-z]+(?:'[A-Za-z]+)?$")
STRESS_RE = re.compile(r"^[US]+$")
VALID_FEET = {
    "monometer": 1,
    "dimeter": 2,
    "trimeter": 3,
    "tetrameter": 4,
    "pentameter": 5,
    "hexameter": 6,
}
LINE_NAME_BY_FEET = {v: k for k, v in VALID_FEET.items()}


@dataclass
class LLMRefinement:
    meter_name: str
    confidence: float
    analysis_hint: str
    token_patterns: List[str]


def _extract_json_obj(content: str) -> Optional[dict]:
    content = (content or "").strip()
    if not content:
        return None
    if content.startswith("```"):
        content = content.strip("`")
        content = content.replace("json\n", "", 1).strip()
    try:
        obj = json.loads(content)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass

    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(content[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(content[start : i + 1])
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None
    return None


class LLMRefiner:
    def __init__(self, endpoint: str, model: str, api_key: str = "") -> None:
        self.endpoint = endpoint.strip()
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.last_error = ""
        self.last_raw_response = ""

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _system_prompt(self) -> str:
        return (
            "You are an expert in English poetic meter and scansion. "
            "Analyze stress at the whole-line level, not per-word dictionary stress in isolation. "
            "Return ONLY strict JSON with this exact top-level shape: {\"results\":[...]}. "
            "Return exactly one result object per input line_no; do not omit any line. "
            "Each result must include: line_no (int), meter_name (string), confidence (0..1 number), "
            "analysis_hint (<=220 chars), token_stress_patterns (list of strings). "
            "token_stress_patterns length must equal token count, and each token pattern must contain only U/S "
            "with exact length equal to token_syllables for that token. "
            "Use canonical meter names when possible, e.g. 'iambic pentameter', "
            "'trochaic tetrameter', 'anapestic trimeter', 'dactylic hexameter'. "
            "No prose outside JSON."
        )

    def _canonical_meter_name(self, raw: str, base: LineAnalysis) -> str:
        s = " ".join((raw or "").strip().lower().split())
        if not s:
            return ""
        s = s.replace("iambs", "iambic")
        s = s.replace("trochees", "trochaic")
        s = s.replace("anapests", "anapestic")
        s = s.replace("dactyls", "dactylic")

        syllables = sum(len(p) for p in (base.token_patterns or []))
        if syllables <= 0:
            syllables = 10

        m = re.search(r"\b(iambic|trochaic|anapestic|dactylic)\b", s)
        if not m:
            return s
        foot_name = m.group(1)

        m2 = re.search(r"\b(monometer|dimeter|trimeter|tetrameter|pentameter|hexameter)\b", s)
        if m2:
            feet_name = m2.group(1)
            if foot_name == "iambic" and 9 <= syllables <= 11:
                feet_name = "pentameter"
            return "{} {}".format(foot_name, feet_name)

        m3 = re.search(r"\b([1-6])\s*[- ]*foot\b", s)
        if m3:
            feet = int(m3.group(1))
            if foot_name == "iambic" and 9 <= syllables <= 11:
                feet = 5
            return "{} {}".format(foot_name, LINE_NAME_BY_FEET.get(feet, "pentameter"))

        unit = 3 if foot_name in {"anapestic", "dactylic"} else 2
        feet = int(round(float(syllables) / float(unit)))
        feet = max(1, min(6, feet))
        if foot_name == "iambic" and 9 <= syllables <= 11:
            feet = 5
        return "{} {}".format(foot_name, LINE_NAME_BY_FEET.get(feet, "pentameter"))

    def _debug_path(self) -> str:
        path = os.environ.get("METERMETER_LLM_DEBUG_PATH", "").strip()
        if path:
            return path
        return "/tmp/metermeter_llm_debug.json"

    def _write_debug_dump(self, reason: str, payload: dict, raw: str = "") -> str:
        path = self._debug_path()
        out = {
            "reason": reason,
            "endpoint": self.endpoint,
            "model": self.model,
            "request_payload": payload,
            "raw_response": raw,
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=True)
        except Exception:
            return ""
        return path

    def _normalize_token_pattern(self, raw_pat: str, expected_len: int, baseline_pat: str) -> str:
        p = (raw_pat or "").strip().upper()
        if p:
            # Some models emit separators like "U.S" or "S-U"; keep only stress symbols.
            p = "".join(ch for ch in p if ch in {"U", "S"})
        if expected_len <= 0:
            return ""
        if not p or not STRESS_RE.match(p):
            return ""
        if len(p) == expected_len:
            return p
        # Practical repair: many models return per-token U/S rather than per-syllable.
        # Use baseline syllable scaffold for length correction while preserving valid stress alphabet.
        if baseline_pat and len(baseline_pat) == expected_len and STRESS_RE.match(baseline_pat):
            return baseline_pat
        if len(p) == 1:
            if p == "S":
                return ("U" * max(0, expected_len - 1)) + "S"
            return "U" * expected_len
        return ""

    def refine_lines(
        self,
        baselines: List[LineAnalysis],
        timeout_ms: int,
        temperature: float,
    ) -> Dict[int, LLMRefinement]:
        if not baselines:
            return {}
        if self.endpoint.startswith("mock://"):
            out: Dict[int, LLMRefinement] = {}
            for b in baselines:
                out[int(b.line_no)] = LLMRefinement(
                    meter_name=(b.meter_name or "").strip().lower(),
                    confidence=max(0.0, min(1.0, float(b.confidence))),
                    analysis_hint="mock",
                    token_patterns=list(b.token_patterns or []),
                )
            return out
        timeout_s = max(0.2, float(timeout_ms) / 1000.0)

        lines = []
        for b in baselines:
            token_syllables = [len(p) for p in (b.token_patterns or [])]
            lines.append(
                {
                    "line_no": int(b.line_no),
                    "line_text": b.source_text,
                    "tokens": b.tokens,
                    "token_syllables": token_syllables,
                    "baseline_meter": b.meter_name,
                    "baseline_confidence": float(b.confidence),
                }
            )

        # Scale completion budget with batch size to reduce truncation risk on strict JSON output.
        max_tokens = min(3200, max(900, 220 * len(lines) + 300))
        payload = {
            "model": self.model,
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": json.dumps({"lines": lines}, ensure_ascii=True)},
            ],
        }
        req = urllib.request.Request(
            url=self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RuntimeError(f"llm_http_error: {exc.code} {detail}")
        self.last_raw_response = raw

        try:
            data = json.loads(raw)
        except Exception:
            path = self._write_debug_dump("response_not_json", payload, raw)
            msg = "llm_invalid_response: response_not_json"
            if path:
                msg += " (debug_dump={})".format(path)
            self.last_error = msg
            raise RuntimeError(msg)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            path = self._write_debug_dump("missing_choices", payload, raw)
            msg = "llm_invalid_response: missing_choices"
            if path:
                msg += " (debug_dump={})".format(path)
            self.last_error = msg
            raise RuntimeError(msg)
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(msg, dict):
            path = self._write_debug_dump("missing_message", payload, raw)
            err = "llm_invalid_response: missing_message"
            if path:
                err += " (debug_dump={})".format(path)
            self.last_error = err
            raise RuntimeError(err)
        content = msg.get("content")
        if not isinstance(content, str):
            path = self._write_debug_dump("missing_content", payload, raw)
            err = "llm_invalid_response: missing_content"
            if path:
                err += " (debug_dump={})".format(path)
            self.last_error = err
            raise RuntimeError(err)

        obj = _extract_json_obj(content)
        if obj is None:
            path = self._write_debug_dump("content_not_json_object", payload, raw)
            err = "llm_invalid_response: content_not_json_object"
            if path:
                err += " (debug_dump={})".format(path)
            self.last_error = err
            raise RuntimeError(err)
        results = obj.get("results")
        if not isinstance(results, list):
            path = self._write_debug_dump("missing_results", payload, raw)
            err = "llm_invalid_response: missing_results"
            if path:
                err += " (debug_dump={})".format(path)
            self.last_error = err
            raise RuntimeError(err)

        baseline_by_no = {int(b.line_no): b for b in baselines}
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
            base = baseline_by_no.get(line_no)
            if base is None:
                continue

            meter_name = item.get("meter_name") or item.get("final_meter") or ""
            if not isinstance(meter_name, str) or not meter_name.strip():
                continue
            meter_name = self._canonical_meter_name(meter_name, base)

            conf = item.get("confidence") or item.get("meter_confidence")
            if not isinstance(conf, (int, float)):
                continue
            conf = max(0.0, min(1.0, float(conf)))

            hint = item.get("analysis_hint") or ""
            if not isinstance(hint, str):
                hint = ""
            hint = " ".join(hint.split())[:220]

            token_patterns = item.get("token_stress_patterns") or item.get("token_patterns") or []
            if not isinstance(token_patterns, list):
                continue
            if len(token_patterns) > len(base.tokens):
                token_patterns = token_patterns[: len(base.tokens)]
            elif len(token_patterns) < len(base.tokens):
                for i in range(len(token_patterns), len(base.tokens)):
                    fallback = base.token_patterns[i] if i < len(base.token_patterns) else "U"
                    token_patterns.append(fallback)
            normalized: List[str] = []
            token_syllables = [len(p) for p in (base.token_patterns or [])]
            if len(token_syllables) != len(base.tokens):
                token_syllables = [1 for _ in base.tokens]
            if len(token_syllables) != len(token_patterns):
                continue
            ok = True
            for i, p in enumerate(token_patterns):
                if not isinstance(p, str):
                    ok = False
                    break
                baseline_pat = base.token_patterns[i] if i < len(base.token_patterns) else ""
                norm = self._normalize_token_pattern(p, int(token_syllables[i]), baseline_pat)
                if not norm:
                    ok = False
                    break
                normalized.append(norm)
            if not ok or len(normalized) != len(base.tokens):
                continue

            out[line_no] = LLMRefinement(
                meter_name=meter_name,
                confidence=conf,
                analysis_hint=hint,
                token_patterns=normalized,
            )

        # Some models occasionally omit/garble one line in a batch even when others are valid.
        # Retry missing lines individually to improve robustness without engine fallback.
        if 0 < len(out) < len(baselines):
            missing = [b for b in baselines if int(b.line_no) not in out]
            for b in missing:
                try:
                    single = self.refine_lines([b], timeout_ms=timeout_ms, temperature=temperature)
                    if int(b.line_no) in single:
                        out[int(b.line_no)] = single[int(b.line_no)]
                except Exception:
                    continue

        if not out:
            path = self._write_debug_dump("results_failed_validation", payload, raw)
            err = "llm_invalid_response: results_failed_validation"
            if path:
                err += " (debug_dump={})".format(path)
            self.last_error = err
            raise RuntimeError(err)
        self.last_error = ""
        return out
