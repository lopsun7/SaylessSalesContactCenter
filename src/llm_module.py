#!/usr/bin/env python3
"""OpenAI-backed response generation helpers."""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Tuple

try:
    from pydantic import BaseModel, Field, ValidationError
except ImportError as exc:  # pragma: no cover - import error path
    raise RuntimeError(
        "pydantic is required for LLM payload validation. Install with: pip install pydantic"
    ) from exc


class _SchemaModel(BaseModel):
    """Pydantic model with extra-key ignore across v1/v2."""

    if hasattr(BaseModel, "model_config"):
        model_config = {"extra": "ignore"}  # type: ignore[assignment]
    else:
        class Config:
            extra = "ignore"


class MemoryPatchModel(_SchemaModel):
    use_case: str = ""
    budget: str = ""
    priority: str = ""
    device: str = ""
    intent: str = ""
    objections: List[str] = Field(default_factory=list)
    trust_concerns: List[str] = Field(default_factory=list)
    notes: str = ""


class NormalizedSlotsModel(_SchemaModel):
    use_case: str = "unknown"
    budget_tier: str = "unknown"
    priority: str = "unknown"
    device: str = "unknown"


class StateSlotsPatchModel(_SchemaModel):
    use_case: str = "unknown"
    budget_tier: str = "unknown"
    priority: str = "unknown"
    device: str = "unknown"
    trust_sensitive: bool = False
    conflict_flags: List[str] = Field(default_factory=list)


class StatePatchModel(_SchemaModel):
    stage: str = ""
    slots: StateSlotsPatchModel = Field(default_factory=StateSlotsPatchModel)
    unresolved_objections: List[str] = Field(default_factory=list)


class SignalsAmbiguityModel(_SchemaModel):
    is_ambiguous: bool = False
    reason: str = ""


class SignalsConfidenceModel(_SchemaModel):
    overall: float = 0.0
    constraint_confidence: float = 0.0


class SignalsModel(_SchemaModel):
    intent: str = "unknown"
    objections: List[str] = Field(default_factory=list)
    trust_flags: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    ambiguity: SignalsAmbiguityModel = Field(default_factory=SignalsAmbiguityModel)
    engagement: str = "medium"
    confidence: SignalsConfidenceModel = Field(default_factory=SignalsConfidenceModel)


class NextActionModel(_SchemaModel):
    stage: str = ""
    assistant_intent: str = ""
    ask_clarification: bool = False
    reason: str = ""


class IngestionPayloadModel(_SchemaModel):
    memory_patch: MemoryPatchModel = Field(default_factory=MemoryPatchModel)
    normalized_slots: NormalizedSlotsModel = Field(default_factory=NormalizedSlotsModel)
    state_patch: StatePatchModel = Field(default_factory=StatePatchModel)
    signals: SignalsModel = Field(default_factory=SignalsModel)
    next_action: NextActionModel = Field(default_factory=NextActionModel)


class DimensionScoresModel(_SchemaModel):
    need_discovery: float = 2
    recommendation_relevance: float = 2
    objection_handling: float = 2
    trust_risk: float = 2
    tone_control: float = 3
    factual_correctness: float = 3
    closing_appropriateness: float = 2


class EvaluationPayloadModel(_SchemaModel):
    dimension_scores: DimensionScoresModel = Field(default_factory=DimensionScoresModel)
    outcome_label: str = "neutral"
    failure_tags: List[str] = Field(default_factory=list)
    root_cause_layer: str = "evaluation"
    root_cause_kind: str = "none"
    notes: str = ""


class ChangeItemModel(_SchemaModel):
    change_id: str = ""
    apply: str = ""
    expected_effect: str = ""


class CandidateUpdatePayloadModel(_SchemaModel):
    candidate_policy: Dict[str, Any] = Field(default_factory=dict)
    candidate_script_pack: Dict[str, Any] = Field(default_factory=dict)
    policy_changes: List[ChangeItemModel] = Field(default_factory=list)
    script_changes: List[ChangeItemModel] = Field(default_factory=list)
    policy_signals: Dict[str, Any] = Field(default_factory=dict)
    script_signals: Dict[str, Any] = Field(default_factory=dict)


class CandidateGatePayloadModel(_SchemaModel):
    apply_candidate: bool = False
    apply_policy: bool = False
    apply_script_pack: bool = False
    confidence: float = 0.0
    reasons: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)
    notes: str = ""


class OpenAIChatClient:
    """Minimal OpenAI Chat Completions client using stdlib HTTP."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 220,
        timeout_s: int = 30,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.timeout_s = timeout_s
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")

        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY is required for llm mode. "
                "Set it in environment or pass api_key explicitly."
            )

    def _post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = urllib.request.Request(
            url=url,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"OpenAI API error {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI network error: {exc}") from exc

    def _stream_chat_completions(
        self,
        payload: Dict[str, Any],
        on_text_chunk: Callable[[str], None],
        stop_event: threading.Event | None = None,
    ) -> Tuple[str, bool]:
        req = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload).encode("utf-8"),
        )
        full_text = ""
        interrupted = False
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                # Put the underlying socket in short-timeout mode so stop_event
                # can interrupt streaming quickly even when SSE gaps occur.
                try:
                    raw = getattr(resp, "fp", None)
                    if raw is not None:
                        raw_inner = getattr(raw, "raw", None)
                        if raw_inner is not None and hasattr(raw_inner, "_sock"):
                            raw_inner._sock.settimeout(0.2)  # type: ignore[attr-defined]
                except Exception:
                    pass

                while True:
                    if stop_event is not None and stop_event.is_set():
                        interrupted = True
                        break

                    try:
                        raw_line = resp.readline()
                    except (socket.timeout, TimeoutError, OSError, ValueError) as exc:
                        if "timed out" in str(exc).lower():
                            continue
                        raise
                    if not raw_line:
                        break

                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = event.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    token = delta.get("content", "")
                    if not isinstance(token, str) or not token:
                        continue
                    full_text += token
                    on_text_chunk(token)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            raise RuntimeError(f"OpenAI API error {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI network error: {exc}") from exc

        return full_text.strip(), interrupted

    def _extract_text(self, data: Dict[str, Any]) -> str:
        choices: List[Dict[str, Any]] = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"OpenAI response missing choices: {data}")

        message = choices[0].get("message", {})
        content = message.get("content", "")

        if isinstance(content, list):
            text_blocks = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
            content = "".join(text_blocks)

        text = str(content).strip()
        if not text:
            raise RuntimeError(f"OpenAI response text empty: {data}")
        return text

    @staticmethod
    def _model_validate(model_cls: Any, payload: Dict[str, Any]) -> Any:
        if hasattr(model_cls, "model_validate"):
            return model_cls.model_validate(payload)
        return model_cls.parse_obj(payload)

    @staticmethod
    def _model_dump(model: Any) -> Dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump()
        return model.dict()

    def _post_json_validated(
        self,
        *,
        payload: Dict[str, Any],
        schema_context: str,
        validate_fn,
    ) -> Dict[str, Any]:
        data = self._post_json(f"{self.base_url}/chat/completions", payload)
        first_text = self._extract_text(data)
        try:
            first_parsed = self._parse_first_json_object(first_text)
            return validate_fn(first_parsed)
        except Exception as first_exc:
            retry_payload = dict(payload)
            retry_messages = list(payload.get("messages", []))
            retry_messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": first_text[:2000],
                    },
                    {
                        "role": "user",
                        "content": (
                            "Your previous output was invalid JSON or failed schema validation. "
                            "Return ONLY one valid JSON object that matches the schema hint exactly. "
                            "No prose, no markdown, no truncation."
                        ),
                    },
                ]
            )
            retry_payload["messages"] = retry_messages
            retry_payload["temperature"] = 0.0
            retry_payload["max_tokens"] = max(int(payload.get("max_tokens", 256)), 420)

            retry_data = self._post_json(f"{self.base_url}/chat/completions", retry_payload)
            retry_text = self._extract_text(retry_data)
            try:
                retry_parsed = self._parse_first_json_object(retry_text)
                return validate_fn(retry_parsed)
            except Exception as second_exc:
                raise RuntimeError(
                    f"{schema_context} parse/validation failed after one retry. "
                    f"first_error={first_exc}; second_error={second_exc}; "
                    f"retry_output={retry_text[:800]}"
                ) from second_exc

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped

        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _close_truncated_json(candidate: str) -> str:
        text = candidate
        start = text.find("{")
        if start >= 0:
            text = text[start:]

        stack: List[str] = []
        in_string = False
        escape = False

        for char in text:
            if in_string:
                if escape:
                    escape = False
                    continue
                if char == "\\":
                    escape = True
                elif char == "\"":
                    in_string = False
                continue

            if char == "\"":
                in_string = True
            elif char == "{":
                stack.append("}")
            elif char == "[":
                stack.append("]")
            elif char in {"}", "]"} and stack:
                if char == stack[-1]:
                    stack.pop()
                else:
                    # Handle mismatched closers by dropping nearest expected.
                    try:
                        idx = len(stack) - 1 - stack[::-1].index(char)
                        del stack[idx]
                    except ValueError:
                        pass

        repaired = text
        if in_string:
            repaired += "\""

        repaired = re.sub(r",\s*$", "", repaired)
        if re.search(r":\s*$", repaired):
            repaired += " null"

        if stack:
            repaired += "".join(reversed(stack))

        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        return repaired

    @staticmethod
    def _parse_first_json_object(text: str) -> Dict[str, Any]:
        cleaned = OpenAIChatClient._strip_code_fences(text)
        candidates: List[str] = []

        def _add_candidate(value: str) -> None:
            v = value.strip()
            if v and v not in candidates:
                candidates.append(v)

        _add_candidate(cleaned)

        start = cleaned.find("{")
        if start >= 0:
            _add_candidate(cleaned[start:])

        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            _add_candidate(match.group(0))

        if "}" in cleaned:
            _add_candidate(cleaned[: cleaned.rfind("}") + 1])

        parse_errors: List[str] = []
        for candidate in candidates:
            for variant in (candidate, OpenAIChatClient._close_truncated_json(candidate)):
                try:
                    parsed = json.loads(variant)
                except json.JSONDecodeError as exc:
                    parse_errors.append(str(exc))
                    continue
                if isinstance(parsed, dict):
                    return parsed

        raise RuntimeError(
            "Invalid JSON in LLM output after recovery attempts: "
            f"{cleaned}\nParse errors: {parse_errors[-3:]}"
        )

    def _validate_ingestion_payload(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        try:
            model = self._model_validate(IngestionPayloadModel, parsed)
        except ValidationError as exc:
            raise RuntimeError(f"Ingestion schema validation error: {exc}") from exc
        return self._model_dump(model)

    def _validate_evaluation_payload(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        try:
            model = self._model_validate(EvaluationPayloadModel, parsed)
        except ValidationError as exc:
            raise RuntimeError(f"Evaluation schema validation error: {exc}") from exc
        return self._model_dump(model)

    def _validate_candidate_payload(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        try:
            model = self._model_validate(CandidateUpdatePayloadModel, parsed)
        except ValidationError as exc:
            raise RuntimeError(f"Candidate update schema validation error: {exc}") from exc
        return self._model_dump(model)

    def _validate_candidate_gate_payload(self, parsed: Dict[str, Any]) -> Dict[str, Any]:
        try:
            model = self._model_validate(CandidateGatePayloadModel, parsed)
        except ValidationError as exc:
            raise RuntimeError(f"Candidate gate schema validation error: {exc}") from exc
        return self._model_dump(model)

    def ingest_user_turn(
        self,
        *,
        state: Dict[str, Any],
        user_text: str,
    ) -> Dict[str, Any]:
        """LLM-first ingestion that updates free-form memory and model-directed state/action."""
        system_prompt = (
            "You are an information extractor for a sales call agent. "
            "Return ONLY valid JSON and no extra text."
        )

        schema_hint = {
            "memory_patch": {
                "use_case": "string",
                "budget": "string",
                "priority": "string",
                "device": "string",
                "intent": "string",
                "objections": ["string"],
                "trust_concerns": ["string"],
                "notes": "string",
            },
            "normalized_slots": {
                "use_case": "unknown|commute|calls|gym|running|music|work|casual|gaming",
                "budget_tier": "unknown|budget|mid|premium",
                "priority": "unknown|anc|calls|fit|battery|value|sound|reliability",
                "device": "unknown|ios|android",
            },
            "state_patch": {
                "stage": "string",
                "slots": {
                    "use_case": "string",
                    "budget_tier": "string",
                    "priority": "string",
                    "device": "string",
                    "trust_sensitive": "bool",
                    "conflict_flags": ["string"],
                },
                "unresolved_objections": ["string"],
            },
            "signals": {
                "intent": "explore|compare|buy|defer|reject|unknown",
                "objections": ["price|authenticity|time|returns|brand_loyalty"],
                "trust_flags": ["authenticity|warranty|returns"],
                "conflicts": ["price_vs_quality|feature_vs_budget"],
                "ambiguity": {"is_ambiguous": "bool", "reason": "string"},
                "engagement": "low|medium|high",
                "confidence": {"overall": "0-1", "constraint_confidence": "0-1"},
            },
            "next_action": {
                "stage": "string",
                "assistant_intent": "string",
                "ask_clarification": "bool",
                "reason": "string",
            },
        }

        user_prompt = (
            "Extract and update the latest customer information from this turn. "
            "Prefer filling free-form memory fields over forcing classification. "
            "Also provide a direct state patch and next action decision for autonomous control. "
            "If unsure, keep fields empty instead of inventing details.\n"
            f"Current state memory: {json.dumps(state.get('memory', {}), ensure_ascii=True)}\n"
            f"Current structured slots: {json.dumps(state.get('slots', {}), ensure_ascii=True)}\n"
            f"Current stage: {state.get('stage', '')}\n"
            f"Current unresolved objections: {json.dumps(state.get('unresolved_objections', []), ensure_ascii=True)}\n"
            f"User turn: {user_text}\n"
            f"Output JSON schema hint: {json.dumps(schema_hint, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 420,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        parsed = self._post_json_validated(
            payload=payload,
            schema_context="ingest_user_turn",
            validate_fn=self._validate_ingestion_payload,
        )
        return parsed

    @staticmethod
    def _normalize_evaluation_payload(parsed: Dict[str, Any]) -> Dict[str, Any]:
        def _score(name: str, default: int) -> int:
            value = parsed.get("dimension_scores", {}).get(name, default)
            try:
                number = int(round(float(value)))
            except (TypeError, ValueError):
                number = default
            return max(0, min(4, number))

        dim_scores = {
            "need_discovery": _score("need_discovery", 2),
            "recommendation_relevance": _score("recommendation_relevance", 2),
            "objection_handling": _score("objection_handling", 2),
            "trust_risk": _score("trust_risk", 2),
            "tone_control": _score("tone_control", 3),
            "factual_correctness": _score("factual_correctness", 3),
            "closing_appropriateness": _score("closing_appropriateness", 2),
        }

        outcome = str(parsed.get("outcome_label", "neutral")).strip().lower()
        if outcome not in {"negative", "neutral", "positive"}:
            outcome = "neutral"

        tags_raw = parsed.get("failure_tags", [])
        tags: List[str] = []
        if isinstance(tags_raw, list):
            for item in tags_raw:
                if isinstance(item, str):
                    cleaned = item.strip()
                    if cleaned and cleaned not in tags:
                        tags.append(cleaned)

        root_layer = str(parsed.get("root_cause_layer", "evaluation")).strip() or "evaluation"
        root_kind = str(parsed.get("root_cause_kind", "none")).strip() or "none"
        notes = str(parsed.get("notes", "")).strip()

        return {
            "dimension_scores": dim_scores,
            "outcome_label": outcome,
            "failure_tags": tags,
            "root_cause_layer": root_layer,
            "root_cause_kind": root_kind,
            "notes": notes,
        }

    def judge_live_call(
        self,
        *,
        session_id: str,
        iteration: str,
        state: Dict[str, Any],
        transcript: List[Dict[str, Any]],
        last_user_turn: str,
        last_response: str,
    ) -> Dict[str, Any]:
        """LLM judge for live-call outcomes and failure taxonomy."""
        system_prompt = (
            "You are a strict evaluator for a wireless-earbuds sales assistant. "
            "Return ONLY valid JSON and no extra text. "
            "Score consistently and avoid leniency."
        )
        schema_hint = {
            "dimension_scores": {
                "need_discovery": "0-4",
                "recommendation_relevance": "0-4",
                "objection_handling": "0-4",
                "trust_risk": "0-4",
                "tone_control": "0-4",
                "factual_correctness": "0-4",
                "closing_appropriateness": "0-4",
            },
            "outcome_label": "negative|neutral|positive",
            "failure_tags": [
                "missed_need_discovery",
                "premature_closing",
                "trust_not_addressed",
                "wrong_objection_handling",
                "conflicting_signals_mishandled",
                "information_overload",
                "recommendation_mismatch",
                "hallucinated_claims",
            ],
            "root_cause_layer": "state_tracking|strategy_selection|content_planning|response_generation|evaluation|none",
            "root_cause_kind": "decision|expression|none",
            "notes": "short string",
        }
        payload_context = {
            "state": {
                "stage": state.get("stage"),
                "slots": state.get("slots"),
                "unresolved_objections": state.get("unresolved_objections", []),
                "memory": state.get("memory", {}),
            },
            "last_user_turn": last_user_turn,
            "last_response": last_response,
            "transcript_tail": transcript[-12:],
        }
        user_prompt = (
            "Evaluate the assistant performance on this live call and produce one JSON object. "
            "Flag concrete failures, keep scores grounded in transcript evidence, and avoid optimistic bias.\n"
            f"Context JSON: {json.dumps(payload_context, ensure_ascii=True)}\n"
            f"Output JSON schema hint: {json.dumps(schema_hint, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 380,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        parsed = self._post_json_validated(
            payload=payload,
            schema_context="judge_live_call",
            validate_fn=self._validate_evaluation_payload,
        )
        normalized = self._normalize_evaluation_payload(parsed)
        normalized["test_id"] = session_id
        normalized["iteration"] = iteration
        return normalized

    def propose_candidate_updates(
        self,
        *,
        current_policy: Dict[str, Any],
        current_script_pack: Dict[str, Any],
        live_evaluation: Dict[str, Any],
        learning_report: Dict[str, Any],
        max_policy_changes: int,
        max_script_changes: int,
    ) -> Dict[str, Any]:
        system_prompt = (
            "You are a self-improvement optimizer for a sales-call agent. "
            "Return ONLY valid JSON and no extra text. "
            "Propose minimal high-impact edits to policy/script pack to fix failures "
            "while keeping non-regression behavior."
        )

        schema_hint = {
            "candidate_policy": {"...": "full policy JSON object"},
            "candidate_script_pack": {"...": "full script-pack JSON object"},
            "policy_changes": [
                {"change_id": "short_id", "apply": "what changed", "expected_effect": "why"}
            ],
            "script_changes": [
                {"change_id": "short_id", "apply": "what changed", "expected_effect": "why"}
            ],
            "policy_signals": {"...": "optional diagnostics"},
            "script_signals": {"...": "optional diagnostics"},
        }

        payload_context = {
            "max_policy_changes": max_policy_changes,
            "max_script_changes": max_script_changes,
            "live_evaluation": live_evaluation,
            "learning_report": learning_report,
            "current_policy": current_policy,
            "current_script_pack": current_script_pack,
        }

        user_prompt = (
            "Generate one candidate update package. "
            "Output full candidate_policy and candidate_script_pack objects. "
            "Keep untouched sections unchanged. "
            "Do not exceed the requested change-count limits. "
            "Prefer concise adjustments over broad rewrites.\n"
            f"Context JSON: {json.dumps(payload_context, ensure_ascii=True)}\n"
            f"Output JSON schema hint: {json.dumps(schema_hint, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": 1600,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        parsed = self._post_json_validated(
            payload=payload,
            schema_context="propose_candidate_updates",
            validate_fn=self._validate_candidate_payload,
        )
        return parsed

    def judge_candidate_updates(
        self,
        *,
        live_evaluation: Dict[str, Any],
        current_policy: Dict[str, Any],
        current_script_pack: Dict[str, Any],
        candidate_policy: Dict[str, Any],
        candidate_script_pack: Dict[str, Any],
        policy_changes: List[Dict[str, Any]],
        script_changes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        system_prompt = (
            "You are a strict release gate for self-improvement candidates in a sales-call agent. "
            "Return ONLY valid JSON and no extra text. "
            "Approve only when edits are targeted, coherent, and likely to improve the observed failure "
            "without high regression risk."
        )

        schema_hint = {
            "apply_candidate": "bool",
            "apply_policy": "bool",
            "apply_script_pack": "bool",
            "confidence": "0-1",
            "reasons": ["short string"],
            "risk_flags": ["short string"],
            "notes": "short string",
        }

        payload_context = {
            "live_evaluation": live_evaluation,
            "current_policy": current_policy,
            "current_script_pack": current_script_pack,
            "candidate_policy": candidate_policy,
            "candidate_script_pack": candidate_script_pack,
            "policy_changes": policy_changes,
            "script_changes": script_changes,
        }

        user_prompt = (
            "Decide whether this candidate should be applied now. "
            "If rejecting, set all apply flags false and explain why. "
            "If partially safe, allow only the safe component.\n"
            f"Context JSON: {json.dumps(payload_context, ensure_ascii=True)}\n"
            f"Output JSON schema hint: {json.dumps(schema_hint, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        parsed = self._post_json_validated(
            payload=payload,
            schema_context="judge_candidate_updates",
            validate_fn=self._validate_candidate_gate_payload,
        )

        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        parsed["confidence"] = max(0.0, min(1.0, confidence))
        return parsed

    def generate_response_autonomous(
        self,
        *,
        state: Dict[str, Any],
        user_text: str,
        policy: Dict[str, Any],
        script_pack: Dict[str, Any],
        catalog: Dict[str, Any],
    ) -> str:
        """Generate one reply directly from dialogue/state without rule planner artifacts."""
        system_prompt = (
            "You are a concise ecommerce sales assistant for wireless earbuds. "
            "Drive the conversation autonomously: ask only the most useful next question when details are missing, "
            "or make a concrete recommendation when confident. "
            "Do not invent product facts beyond provided context. "
            "Keep a practical, non-pushy tone and stay under the soft word limit. "
            "Respond in English only."
        )

        payload_context = {
            "user_text": user_text,
            "state": {
                "stage": state.get("stage"),
                "slots": state.get("slots"),
                "memory": state.get("memory", {}),
                "unresolved_objections": state.get("unresolved_objections", []),
                "history_tail": state.get("history", [])[-8:],
            },
            "policy_constraints": {
                "max_words_soft": policy.get("rules", {}).get("response_limits", {}).get("max_words_soft", 80),
            },
            "script_style": script_pack.get("style", {}),
            "catalog": catalog,
        }

        user_prompt = (
            "Generate exactly one natural agent reply for the next turn.\n"
            "Context JSON:\n"
            f"{json.dumps(payload_context, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        data = self._post_json(f"{self.base_url}/chat/completions", payload)
        return self._extract_text(data)

    def stream_generate_response_autonomous(
        self,
        *,
        state: Dict[str, Any],
        user_text: str,
        policy: Dict[str, Any],
        script_pack: Dict[str, Any],
        catalog: Dict[str, Any],
        on_text_chunk: Callable[[str], None],
        stop_event: threading.Event | None = None,
    ) -> Tuple[str, bool]:
        """Generate one autonomous reply via SSE stream."""
        system_prompt = (
            "You are a concise ecommerce sales assistant for wireless earbuds. "
            "Drive the conversation autonomously: ask only the most useful next question when details are missing, "
            "or make a concrete recommendation when confident. "
            "Do not invent product facts beyond provided context. "
            "Keep a practical, non-pushy tone and stay under the soft word limit. "
            "Respond in English only."
        )

        payload_context = {
            "user_text": user_text,
            "state": {
                "stage": state.get("stage"),
                "slots": state.get("slots"),
                "memory": state.get("memory", {}),
                "unresolved_objections": state.get("unresolved_objections", []),
                "history_tail": state.get("history", [])[-8:],
            },
            "policy_constraints": {
                "max_words_soft": policy.get("rules", {}).get("response_limits", {}).get("max_words_soft", 80),
            },
            "script_style": script_pack.get("style", {}),
            "catalog": catalog,
        }

        user_prompt = (
            "Generate exactly one natural agent reply for the next turn.\n"
            "Context JSON:\n"
            f"{json.dumps(payload_context, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        return self._stream_chat_completions(payload, on_text_chunk=on_text_chunk, stop_event=stop_event)

    def generate_response(
        self,
        *,
        state: Dict[str, Any],
        strategy: Dict[str, Any],
        plan: Dict[str, Any],
        policy: Dict[str, Any],
        script_pack: Dict[str, Any],
        user_text: str,
        catalog: Dict[str, Any],
    ) -> str:
        """Generate one agent reply grounded on upstream pipeline artifacts."""
        system_prompt = (
            "You are a concise ecommerce sales assistant for wireless earbuds. "
            "Follow the provided strategy and content plan strictly. "
            "Do not invent product facts beyond provided context. "
            "Address objections first when present. "
            "If plan has clarification questions, ask them clearly and briefly. "
            "Respond in English only."
        )

        payload_context = {
            "user_text": user_text,
            "state": {
                "stage": state.get("stage"),
                "slots": state.get("slots"),
                "memory": state.get("memory", {}),
                "unresolved_objections": state.get("unresolved_objections", []),
            },
            "strategy": strategy,
            "plan": plan,
            "policy_constraints": {
                "max_words_soft": policy.get("rules", {}).get("response_limits", {}).get("max_words_soft", 80),
            },
            "script_style": script_pack.get("style", {}),
            "catalog_context": {
                "return_policy_days": catalog.get("return_policy_days"),
                "warranty_months": catalog.get("warranty_months"),
            },
        }

        user_prompt = (
            "Generate exactly one natural agent reply for the next turn. "
            "Keep it practical, non-pushy, and under the soft word limit. "
            "Context JSON:\n"
            f"{json.dumps(payload_context, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        data = self._post_json(f"{self.base_url}/chat/completions", payload)
        return self._extract_text(data)

    def stream_generate_response(
        self,
        *,
        state: Dict[str, Any],
        strategy: Dict[str, Any],
        plan: Dict[str, Any],
        policy: Dict[str, Any],
        script_pack: Dict[str, Any],
        user_text: str,
        catalog: Dict[str, Any],
        on_text_chunk: Callable[[str], None],
        stop_event: threading.Event | None = None,
    ) -> Tuple[str, bool]:
        """Generate response via SSE stream. Returns (full_text, interrupted)."""
        system_prompt = (
            "You are a concise ecommerce sales assistant for wireless earbuds. "
            "Follow the provided strategy and content plan strictly. "
            "Do not invent product facts beyond provided context. "
            "Address objections first when present. "
            "If plan has clarification questions, ask them clearly and briefly. "
            "Respond in English only."
        )

        payload_context = {
            "user_text": user_text,
            "state": {
                "stage": state.get("stage"),
                "slots": state.get("slots"),
                "memory": state.get("memory", {}),
                "unresolved_objections": state.get("unresolved_objections", []),
            },
            "strategy": strategy,
            "plan": plan,
            "policy_constraints": {
                "max_words_soft": policy.get("rules", {}).get("response_limits", {}).get("max_words_soft", 80),
            },
            "script_style": script_pack.get("style", {}),
            "catalog_context": {
                "return_policy_days": catalog.get("return_policy_days"),
                "warranty_months": catalog.get("warranty_months"),
            },
        }

        user_prompt = (
            "Generate exactly one natural agent reply for the next turn. "
            "Keep it practical, non-pushy, and under the soft word limit. "
            "Context JSON:\n"
            f"{json.dumps(payload_context, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        return self._stream_chat_completions(payload, on_text_chunk=on_text_chunk, stop_event=stop_event)
