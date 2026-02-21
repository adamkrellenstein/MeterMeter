import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from .meter_engine import LineAnalysis

WORD_RE = re.compile(r"^[A-Za-z]+(?:'[A-Za-z]+)?$")
STRESS_RE = re.compile(r"^[US]+$")


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

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _system_prompt(self) -> str:
        return (
            "You are an expert in English poetic meter and scansion. "
            "Analyze stress at the whole-line level, not per-word dictionary stress in isolation. "
            "Return ONLY JSON: {results:[...]}. "
            "Each result must include: line_no, meter_name, confidence, analysis_hint, token_stress_patterns. "
            "token_stress_patterns must be a list of U/S strings, one per token, and lengths must match token_syllables. "
            "confidence is 0..1, analysis_hint <= 220 chars."
        )

    def refine_lines(
        self,
        baselines: List[LineAnalysis],
        timeout_ms: int,
        temperature: float,
    ) -> Dict[int, LLMRefinement]:
        if not baselines:
            return {}
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

        payload = {
            "model": self.model,
            "temperature": max(0.0, min(1.0, float(temperature))),
            "max_tokens": 900,
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

        data = json.loads(raw)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return {}
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(msg, dict):
            return {}
        content = msg.get("content")
        if not isinstance(content, str):
            return {}

        obj = _extract_json_obj(content)
        if obj is None:
            return {}
        results = obj.get("results")
        if not isinstance(results, list):
            return {}

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
                meter_name = base.meter_name
            meter_name = meter_name.strip().lower()

            conf = item.get("confidence") or item.get("meter_confidence")
            if not isinstance(conf, (int, float)):
                conf = base.confidence
            conf = max(0.0, min(1.0, float(conf)))

            hint = item.get("analysis_hint") or ""
            if not isinstance(hint, str):
                hint = ""
            hint = " ".join(hint.split())[:220]

            token_patterns = item.get("token_stress_patterns") or item.get("token_patterns") or []
            if not isinstance(token_patterns, list) or len(token_patterns) != len(base.tokens):
                token_patterns = base.token_patterns
            normalized: List[str] = []
            ok = True
            for p in token_patterns:
                if not isinstance(p, str):
                    ok = False
                    break
                p = p.strip().upper()
                if not p or not STRESS_RE.match(p):
                    ok = False
                    break
                normalized.append(p)
            if not ok or len(normalized) != len(base.tokens):
                normalized = base.token_patterns

            out[line_no] = LLMRefinement(
                meter_name=meter_name,
                confidence=conf,
                analysis_hint=hint,
                token_patterns=normalized,
            )

        return out

