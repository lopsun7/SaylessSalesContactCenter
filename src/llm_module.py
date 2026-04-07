#!/usr/bin/env python3
"""OpenAI-backed response generation helpers."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List


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
    def _parse_first_json_object(text: str) -> Dict[str, Any]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Fallback: extract first JSON object from mixed text.
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise RuntimeError(f"Could not parse JSON object from LLM output: {text}")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in LLM output: {text}") from exc

    def ingest_user_turn(
        self,
        *,
        state: Dict[str, Any],
        user_text: str,
    ) -> Dict[str, Any]:
        """LLM-first ingestion that fills free-form memory and optional normalized slots/signals."""
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
            "signals": {
                "intent": "explore|compare|buy|defer|reject|unknown",
                "objections": ["price|authenticity|time|returns|brand_loyalty"],
                "trust_flags": ["authenticity|warranty|returns"],
                "conflicts": ["price_vs_quality|feature_vs_budget"],
                "ambiguity": {"is_ambiguous": "bool", "reason": "string"},
                "engagement": "low|medium|high",
                "confidence": {"overall": "0-1", "constraint_confidence": "0-1"},
            },
        }

        user_prompt = (
            "Extract and update the latest customer information from this turn. "
            "Prefer filling free-form memory fields over forcing classification. "
            "If unsure, keep string fields empty and normalized fields as 'unknown'.\n"
            f"Current state memory: {json.dumps(state.get('memory', {}), ensure_ascii=True)}\n"
            f"Current structured slots: {json.dumps(state.get('slots', {}), ensure_ascii=True)}\n"
            f"User turn: {user_text}\n"
            f"Output JSON schema hint: {json.dumps(schema_hint, ensure_ascii=True)}"
        )

        payload = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 300,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        data = self._post_json(f"{self.base_url}/chat/completions", payload)
        text = self._extract_text(data)
        parsed = self._parse_first_json_object(text)

        # Normalize missing sections.
        parsed.setdefault("memory_patch", {})
        parsed.setdefault("normalized_slots", {})
        parsed.setdefault("signals", {})
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
            "Keep a practical, non-pushy tone and stay under the soft word limit."
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
            "If plan has clarification questions, ask them clearly and briefly."
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
