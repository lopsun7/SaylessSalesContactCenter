#!/usr/bin/env python3
"""Interactive console for live sales calls with optional self-improvement."""

from __future__ import annotations

import argparse
import json
import queue
import select
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from baseline_v0 import (
    initial_state,
    load_json_or_yaml,
    load_policy,
    load_script_pack,
    normalize_policy,
    normalize_script_pack,
)
from llm_module import OpenAIChatClient
from voice_io import VoiceIO, VoiceIOError
from voice_stt import OpenAITranscriptionClient
from voice_tts import OpenAITTSClient


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _build_learning_report(
    *,
    session_id: str,
    policy_version: str,
    script_version: str,
    evaluation: Dict[str, Any],
) -> Dict[str, Any]:
    has_failures = bool(evaluation.get("failure_tags", []))
    return {
        "suite": "live_call_learning",
        "iteration": policy_version,
        "policy_version": policy_version,
        "script_version": script_version,
        "summary": {
            "total": 1,
            "passed": 0 if has_failures else 1,
            "failed": 1 if has_failures else 0,
        },
        "results": [
            {
                "id": session_id,
                "pass": not has_failures,
                "reasons": [f"live_failure:{tag}" for tag in evaluation.get("failure_tags", [])],
                "evaluation": evaluation,
            }
        ],
    }


def _next_version(version: str, prefix: str) -> str:
    if version.startswith(prefix) and version[len(prefix):].isdigit():
        return f"{prefix}{int(version[len(prefix):]) + 1}"
    return f"{version}.next"


def _normalize_candidate_versions(
    *,
    current_policy: Dict[str, Any],
    current_script_pack: Dict[str, Any],
    candidate_policy: Dict[str, Any],
    candidate_script_pack: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], bool, bool]:
    normalized_current_policy = normalize_policy(current_policy)
    normalized_current_scripts = normalize_script_pack(current_script_pack)
    normalized_candidate_policy = normalize_policy(candidate_policy)
    normalized_candidate_scripts = normalize_script_pack(candidate_script_pack)

    policy_compare_current = dict(normalized_current_policy)
    policy_compare_candidate = dict(normalized_candidate_policy)
    policy_compare_current.pop("version", None)
    policy_compare_current.pop("updated_at", None)
    policy_compare_candidate.pop("version", None)
    policy_compare_candidate.pop("updated_at", None)

    script_compare_current = dict(normalized_current_scripts)
    script_compare_candidate = dict(normalized_candidate_scripts)
    script_compare_current.pop("version", None)
    script_compare_current.pop("updated_at", None)
    script_compare_candidate.pop("version", None)
    script_compare_candidate.pop("updated_at", None)

    policy_changed = (
        json.dumps(policy_compare_candidate, sort_keys=True)
        != json.dumps(policy_compare_current, sort_keys=True)
    )
    script_changed = (
        json.dumps(script_compare_candidate, sort_keys=True)
        != json.dumps(script_compare_current, sort_keys=True)
    )

    today = datetime.now(timezone.utc).date().isoformat()
    if policy_changed:
        if str(normalized_candidate_policy.get("version", "")) == str(
            normalized_current_policy.get("version", "")
        ):
            normalized_candidate_policy["version"] = _next_version(
                str(normalized_current_policy.get("version", "v0")),
                "v",
            )
        normalized_candidate_policy["updated_at"] = today

    if script_changed:
        if str(normalized_candidate_scripts.get("version", "")) == str(
            normalized_current_scripts.get("version", "")
        ):
            normalized_candidate_scripts["version"] = _next_version(
                str(normalized_current_scripts.get("version", "s0")),
                "s",
            )
        normalized_candidate_scripts["updated_at"] = today

    return (
        normalized_candidate_policy,
        normalized_candidate_scripts,
        policy_changed,
        script_changed,
    )


def _next_session_id(prefix: str = "live") -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _print_change_list(title: str, items: List[Dict[str, Any]]) -> None:
    print(title)
    if not items:
        print("- none")
        return
    for item in items:
        cycle = item.get("cycle")
        cycle_prefix = f"[cycle {cycle}] " if isinstance(cycle, int) else ""
        print(f"- {cycle_prefix}{item.get('change_id', 'unknown')}: {item.get('apply', 'n/a')}")


class BargeInMonitor:
    """Listen for '/barge <text>' while agent streaming is in progress."""

    def __init__(self, interrupt_event: threading.Event, prefix: str = "/barge ") -> None:
        self.interrupt_event = interrupt_event
        self.prefix = prefix
        self._stop_event = threading.Event()
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=0.2)

    def get_message(self) -> str:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return ""

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                ready, _, _ = select.select([sys.stdin], [], [], 0.1)
            except (OSError, ValueError):
                return
            if not ready:
                continue

            line = sys.stdin.readline()
            if not line:
                continue
            text = line.strip()
            if text.startswith(self.prefix):
                spoken = text[len(self.prefix):].strip()
                if spoken:
                    self._queue.put(spoken)
                    self.interrupt_event.set()
                return


class DuplexASRWorker:
    """Always-on VAD segmentation + STT for duplex voice interaction."""

    def __init__(
        self,
        *,
        voice_io: VoiceIO,
        stt_client: OpenAITranscriptionClient,
        args: argparse.Namespace,
        agent_speaking_event: threading.Event,
    ) -> None:
        self.voice_io = voice_io
        self.stt_client = stt_client
        self.args = args
        self.agent_speaking_event = agent_speaking_event
        self._stop_event = threading.Event()
        self._user_text_queue: queue.Queue[str] = queue.Queue()
        self._barge_text_queue: queue.Queue[str] = queue.Queue()
        self._barge_signal_queue: queue.Queue[int] = queue.Queue()
        self._error_queue: queue.Queue[str] = queue.Queue()
        self._pcm_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=16)
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._stt_thread = threading.Thread(target=self._stt_loop, daemon=True)

        self._pre_roll_frames = max(1, int(max(0, int(args.voice_preroll_ms)) / voice_io.frame_ms))
        self._min_speech_frames = max(1, int(max(80, int(args.voice_min_speech_ms * 0.75)) / voice_io.frame_ms))
        self._silence_frames = max(1, int(max(260, int(args.voice_silence_ms * 0.6)) / voice_io.frame_ms))
        self._max_segment_frames = max(
            self._min_speech_frames + self._silence_frames + 1,
            int(max(1.0, min(float(args.voice_max_seconds), 4.0)) * 1000 / voice_io.frame_ms),
        )
        self._frame_timeout_s = max(0.02, voice_io.frame_ms / 1000.0 * 2.0)

    def start(self) -> None:
        self._capture_thread.start()
        self._stt_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._pcm_queue.put_nowait(None)
        except queue.Full:
            pass
        self._capture_thread.join(timeout=1.2)
        self._stt_thread.join(timeout=1.2)

    def is_alive(self) -> bool:
        return self._capture_thread.is_alive() and self._stt_thread.is_alive()

    def poll_user_text(self) -> str:
        try:
            return self._user_text_queue.get_nowait()
        except queue.Empty:
            return ""

    def poll_barge_text(self) -> str:
        try:
            return self._barge_text_queue.get_nowait()
        except queue.Empty:
            return ""

    def clear_barge_text(self) -> None:
        while True:
            try:
                self._barge_text_queue.get_nowait()
            except queue.Empty:
                return

    def clear_barge_signal(self) -> None:
        while True:
            try:
                self._barge_signal_queue.get_nowait()
            except queue.Empty:
                return

    def poll_barge_signal(self) -> bool:
        try:
            self._barge_signal_queue.get_nowait()
            return True
        except queue.Empty:
            return False

    def poll_error(self) -> str:
        try:
            return self._error_queue.get_nowait()
        except queue.Empty:
            return ""

    def _capture_loop(self) -> None:
        pre_roll: List[bytes] = []
        speaking = False
        speech_frames = 0
        silence_frames = 0
        current: List[bytes] = []

        while not self._stop_event.is_set():
            try:
                frame = self.voice_io.read_mic_frame(timeout_s=self._frame_timeout_s)
            except Exception as exc:
                self._error_queue.put(f"duplex_capture_failed: {exc}")
                time.sleep(0.05)
                continue
            if not frame:
                continue

            pre_roll.append(frame)
            if len(pre_roll) > self._pre_roll_frames:
                pre_roll = pre_roll[-self._pre_roll_frames:]

            speech = self.voice_io.is_speech_frame(frame)
            if not speaking:
                if not speech:
                    continue
                speaking = True
                if self.args.enable_barge_in and self.agent_speaking_event.is_set():
                    try:
                        self._barge_signal_queue.put_nowait(1)
                    except queue.Full:
                        pass
                current = list(pre_roll)
                speech_frames = 1
                silence_frames = 0
                continue

            current.append(frame)
            if speech:
                speech_frames += 1
                silence_frames = 0
            else:
                silence_frames += 1

            should_flush = (
                (speech_frames >= self._min_speech_frames and silence_frames >= self._silence_frames)
                or len(current) >= self._max_segment_frames
            )
            if not should_flush:
                continue

            pcm = b"".join(current)
            speaking = False
            speech_frames = 0
            silence_frames = 0
            current = []
            if not pcm:
                continue
            try:
                self._pcm_queue.put(pcm, timeout=0.2)
            except queue.Full:
                # Drop oldest pending segment to keep latency bounded.
                try:
                    self._pcm_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._pcm_queue.put_nowait(pcm)
                except queue.Full:
                    pass

        if current:
            try:
                self._pcm_queue.put_nowait(b"".join(current))
            except queue.Full:
                pass
        try:
            self._pcm_queue.put_nowait(None)
        except queue.Full:
            pass

    def _stt_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                pcm = self._pcm_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if pcm is None:
                return
            try:
                text = self.stt_client.transcribe_pcm16le(
                    pcm,
                    sample_rate=self.args.voice_sample_rate,
                    language=self.args.stt_language,
                ).strip()
            except Exception as exc:
                self._error_queue.put(str(exc))
                continue
            if text:
                if self.args.enable_barge_in and self.agent_speaking_event.is_set():
                    self._barge_text_queue.put(text)
                else:
                    self._user_text_queue.put(text)


def _poll_stdin_line(timeout_s: float = 0.0) -> str:
    """Try to read one line from stdin without blocking indefinitely."""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], max(0.0, timeout_s))
    except (OSError, ValueError):
        return ""
    if not ready:
        return ""
    line = sys.stdin.readline()
    return line.strip() if line else ""


def _drain_speak_segments(buffer: str) -> Tuple[List[str], str]:
    """Split buffered text into sentence-like chunks for incremental TTS."""
    if not buffer:
        return [], ""

    delimiters = {".", "!", "?", "。", "！", "？", "\n"}
    segments: List[str] = []
    start = 0
    for idx, char in enumerate(buffer):
        if char in delimiters:
            seg = buffer[start: idx + 1].strip()
            if seg:
                segments.append(seg)
            start = idx + 1

    remaining = buffer[start:]
    # Hard flush for long fragments without punctuation.
    if len(remaining) > 42:
        split_idx = remaining.rfind(" ")
        if split_idx > 14:
            seg = remaining[:split_idx].strip()
            if seg:
                segments.append(seg)
            remaining = remaining[split_idx + 1:]

    return segments, remaining


def _merge_memory_patch(state: Dict[str, Any], memory_patch: Dict[str, Any]) -> None:
    memory = state.setdefault(
        "memory",
        {
            "use_case": "",
            "budget": "",
            "priority": "",
            "device": "",
            "intent": "",
            "objections": [],
            "trust_concerns": [],
            "notes": "",
        },
    )

    for field in ("use_case", "budget", "priority", "device", "intent", "notes"):
        value = memory_patch.get(field, "")
        if isinstance(value, str) and value.strip():
            memory[field] = value.strip()

    for field in ("objections", "trust_concerns"):
        values = memory_patch.get(field, [])
        if not isinstance(values, list):
            continue
        existing = memory.setdefault(field, [])
        for item in values:
            if isinstance(item, str):
                cleaned = item.strip()
                if cleaned and cleaned not in existing:
                    existing.append(cleaned)


def _normalize_llm_signals(
    *,
    fallback_signals: Dict[str, Any],
    ingestion: Dict[str, Any],
) -> Dict[str, Any]:
    normalized = json.loads(json.dumps(fallback_signals))

    norm_slots = ingestion.get("normalized_slots", {})
    signals = ingestion.get("signals", {})
    if not isinstance(norm_slots, dict):
        norm_slots = {}
    if not isinstance(signals, dict):
        signals = {}

    if isinstance(norm_slots, dict):
        for slot in ("use_case", "budget_tier", "priority", "device"):
            value = norm_slots.get(slot)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    normalized["constraints"][slot] = cleaned

    intent = signals.get("intent")
    if isinstance(intent, str):
        cleaned = intent.strip()
        if cleaned:
            normalized["intent"] = cleaned

    engagement = signals.get("engagement")
    if isinstance(engagement, str):
        cleaned = engagement.strip()
        if cleaned:
            normalized["engagement"] = cleaned

    objections = signals.get("objections", [])
    if isinstance(objections, list):
        normalized["objections"] = [x.strip() for x in objections if isinstance(x, str) and x.strip()]

    trust_flags = signals.get("trust_flags", [])
    if isinstance(trust_flags, list):
        normalized["trust_flags"] = [x.strip() for x in trust_flags if isinstance(x, str) and x.strip()]

    conflicts = signals.get("conflicts", [])
    if isinstance(conflicts, list):
        normalized["conflicts"] = [x.strip() for x in conflicts if isinstance(x, str) and x.strip()]

    ambiguity = signals.get("ambiguity", {})
    if isinstance(ambiguity, dict):
        is_ambiguous = ambiguity.get("is_ambiguous")
        reason = ambiguity.get("reason")
        if isinstance(is_ambiguous, bool):
            normalized["ambiguity"]["is_ambiguous"] = is_ambiguous
        if isinstance(reason, str):
            normalized["ambiguity"]["reason"] = reason

    confidence = signals.get("confidence", {})
    if isinstance(confidence, dict):
        overall = confidence.get("overall")
        c_conf = confidence.get("constraint_confidence")
        if isinstance(overall, (int, float)):
            normalized["confidence"]["overall"] = max(0.0, min(1.0, float(overall)))
        if isinstance(c_conf, (int, float)):
            normalized["confidence"]["constraint_confidence"] = max(0.0, min(1.0, float(c_conf)))

    return normalized


def _blank_signals() -> Dict[str, Any]:
    return {
        "intent": "unknown",
        "constraints": {
            "budget_tier": "unknown",
            "priority": "unknown",
            "use_case": "unknown",
            "device": "unknown",
        },
        "objections": [],
        "trust_flags": [],
        "ambiguity": {"is_ambiguous": False, "reason": ""},
        "conflicts": [],
        "engagement": "medium",
        "confidence": {"overall": 0.0, "constraint_confidence": 0.0},
    }


def _fallback_evaluation(
    *,
    session_id: str,
    iteration: str,
    state: Dict[str, Any],
    last_user_turn: str,
    last_response: str,
    error: str = "",
) -> Dict[str, Any]:
    failure_tags: List[str] = []
    response_l = last_response.lower()
    if "warranty" not in response_l and any(
        token in last_user_turn.lower() for token in ("authentic", "return", "fake")
    ):
        failure_tags.append("trust_not_addressed")
    if "too expensive" in last_user_turn.lower() and not any(
        token in response_l for token in ("lower", "alternative", "value")
    ):
        failure_tags.append("wrong_objection_handling")

    outcome = "negative" if failure_tags else "neutral"
    notes = "fallback_eval"
    if error:
        notes += f":{error[:180]}"

    return {
        "test_id": session_id,
        "iteration": iteration,
        "dimension_scores": {
            "need_discovery": 2,
            "recommendation_relevance": 2,
            "objection_handling": 2,
            "trust_risk": 2,
            "tone_control": 3,
            "factual_correctness": 3,
            "closing_appropriateness": 2,
        },
        "outcome_label": outcome,
        "failure_tags": failure_tags,
        "root_cause_layer": "evaluation",
        "root_cause_kind": "none",
        "notes": notes,
    }


def _apply_llm_signals_to_state(
    *,
    state: Dict[str, Any],
    signals: Dict[str, Any],
    policy: Dict[str, Any],
    ingestion: Dict[str, Any] | None = None,
    allow_rule_fallback: bool = True,
) -> Dict[str, Any]:
    next_state = json.loads(json.dumps(state))
    next_state["turn_index"] += 1

    constraints = signals.get("constraints", {})
    for slot in ("use_case", "budget_tier", "priority", "device"):
        value = constraints.get(slot, "unknown")
        if isinstance(value, str) and value != "unknown":
            next_state["slots"][slot] = value

    trust_flags = signals.get("trust_flags", [])
    if isinstance(trust_flags, list) and trust_flags:
        next_state["slots"]["trust_sensitive"] = True

    conflicts = signals.get("conflicts", [])
    if isinstance(conflicts, list):
        for conflict in conflicts:
            if isinstance(conflict, str) and conflict not in next_state["slots"]["conflict_flags"]:
                next_state["slots"]["conflict_flags"].append(conflict)

    objections = signals.get("objections", [])
    if isinstance(objections, list):
        for obj in objections:
            if isinstance(obj, str):
                cleaned = obj.strip()
                if cleaned and cleaned not in next_state["unresolved_objections"]:
                    next_state["unresolved_objections"].append(cleaned)

    stage_set_by_llm = False
    state_patch = ingestion.get("state_patch", {}) if isinstance(ingestion, dict) else {}
    if isinstance(state_patch, dict):
        stage = state_patch.get("stage")
        if isinstance(stage, str) and stage.strip():
            next_state["stage"] = stage.strip()
            stage_set_by_llm = True

        slots_patch = state_patch.get("slots", {})
        if isinstance(slots_patch, dict):
            for slot, value in slots_patch.items():
                if slot not in next_state["slots"]:
                    continue
                if slot == "trust_sensitive":
                    next_state["slots"]["trust_sensitive"] = bool(value)
                    continue
                if slot == "conflict_flags" and isinstance(value, list):
                    next_state["slots"]["conflict_flags"] = [
                        item.strip()
                        for item in value
                        if isinstance(item, str) and item.strip()
                    ]
                    continue
                if isinstance(value, str) and value.strip():
                    next_state["slots"][slot] = value.strip()

        unresolved_patch = state_patch.get("unresolved_objections")
        if isinstance(unresolved_patch, list):
            next_state["unresolved_objections"] = [
                item.strip()
                for item in unresolved_patch
                if isinstance(item, str) and item.strip()
            ]

    next_action = ingestion.get("next_action", {}) if isinstance(ingestion, dict) else {}
    if isinstance(next_action, dict):
        stage = next_action.get("stage")
        if isinstance(stage, str) and stage.strip():
            next_state["stage"] = stage.strip()
            stage_set_by_llm = True
        next_state["llm_next_action"] = next_action

    if allow_rule_fallback and not stage_set_by_llm:
        required_slot_names = policy["rules"].get(
            "discovery_required_slots",
            ["use_case", "budget_tier", "priority"],
        )
        missing_required = any(
            next_state["slots"].get(slot, "unknown") == "unknown"
            for slot in required_slot_names
        )

        intent = signals.get("intent", "unknown")
        if next_state["unresolved_objections"]:
            next_state["stage"] = "objection_handling"
        elif missing_required:
            next_state["stage"] = "discovery"
        elif intent == "buy":
            next_state["stage"] = "closing"
        else:
            next_state["stage"] = "recommendation"

    return next_state


def run_live_session(args: argparse.Namespace) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    catalog = load_json_or_yaml(args.catalog)
    policy = load_policy(args.policy)
    script_pack = load_script_pack(args.scripts)

    state = initial_state(args.session_id)
    last_signals: Dict[str, Any] = {}
    last_response = ""
    last_user = ""
    last_ingestion: Dict[str, Any] = {}

    llm_client = OpenAIChatClient(
        model=args.model,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        timeout_s=args.timeout_s,
        base_url=args.base_url,
    )

    voice_io = None
    stt_client = None
    tts_client = None
    if args.io_mode == "voice":
        try:
            voice_io = VoiceIO(
                sample_rate=args.voice_sample_rate,
                frame_ms=args.voice_frame_ms,
                vad_mode=args.voice_vad_mode,
                energy_threshold=args.voice_energy_threshold,
            )
            stt_client = OpenAITranscriptionClient(
                model=args.stt_model,
                timeout_s=args.timeout_s,
                base_url=args.base_url,
            )
            tts_client = OpenAITTSClient(
                model=args.tts_model,
                voice=args.tts_voice,
                response_format=args.tts_response_format,
                speed=args.tts_speed,
                timeout_s=args.timeout_s,
                base_url=args.base_url,
                target_sample_rate=args.voice_sample_rate,
            )
        except (VoiceIOError, ValueError) as exc:
            raise RuntimeError(f"voice mode unavailable: {exc}") from exc

    print("Interactive call started.")
    if args.io_mode == "voice":
        print("Voice mode enabled (full duplex ASR).")
        print("Speak naturally; interruption is speech-driven in real time.")
        print("Type '/end' + Enter to stop, '/state' + Enter to inspect state.")
        print("Microphone stream is always-on in background for faster barge-in capture.")
        print("English-only mode enabled (STT language locked to English).")
        print("Speech barge-in enabled during playback (headphones recommended).")
    else:
        print("Type your message. Use '/end' to finish, '/state' to inspect state.")
        if args.enable_barge_in and args.streaming:
            print(f"During agent streaming, type '{args.barge_prefix}<your text>' to interrupt.")

    if args.io_mode == "voice":
        opening = args.opening_greeting.strip()
        if opening:
            print(f"Agent> {opening}")
            state["history"].append({"role": "agent", "text": opening})
            if tts_client is not None and voice_io is not None:
                try:
                    pcm = tts_client.synthesize_to_pcm(opening)
                    opening_queue: queue.Queue[bytes | None] = queue.Queue()
                    opening_queue.put(pcm)
                    opening_queue.put(None)
                    voice_io.play_pcm_queue(opening_queue, stop_event=None)
                except Exception as exc:
                    print(f"[warn] opening greeting playback failed: {exc}")

    agent_speaking_event = threading.Event()

    def _start_duplex_worker() -> DuplexASRWorker:
        if voice_io is None or stt_client is None:
            raise RuntimeError("voice mode not initialized correctly.")
        worker = DuplexASRWorker(
            voice_io=voice_io,
            stt_client=stt_client,
            args=args,
            agent_speaking_event=agent_speaking_event,
        )
        worker.start()
        return worker

    duplex_worker = _start_duplex_worker() if args.io_mode == "voice" else None

    pending_user_text = ""
    interruption_events: List[Dict[str, Any]] = []
    last_stream_meta: Dict[str, Any] = {"streaming_used": False, "interrupted": False}
    last_listening_hint_at = 0.0

    while True:
        user_text = ""
        if pending_user_text:
            user_text = pending_user_text
            pending_user_text = ""
            prefix = "You(voice)> " if args.io_mode == "voice" else "You> "
            print(f"{prefix}{user_text}")
            last_listening_hint_at = 0.0
        else:
            if args.io_mode == "voice":
                command = _poll_stdin_line(timeout_s=0.0)
                if command.lower() in {"/end", "/quit", "/exit"}:
                    break
                if command.lower() == "/state":
                    print(json.dumps(state, indent=2))
                    continue
                if command.startswith("/"):
                    print(f"[info] unknown command: {command}")
                    continue
                if command:
                    user_text = command
                    print(f"You(typed)> {user_text}")
                    last_listening_hint_at = 0.0
                else:
                    if duplex_worker is None:
                        raise RuntimeError("duplex worker unavailable.")
                    if not duplex_worker.is_alive():
                        print("[warn] duplex ASR worker stopped unexpectedly; restarting.")
                        duplex_worker = _start_duplex_worker()
                        time.sleep(0.05)
                        continue
                    user_text = duplex_worker.poll_user_text().strip()
                    if not user_text:
                        worker_err = duplex_worker.poll_error()
                        if worker_err:
                            if args.fallback_on_llm_error:
                                print(f"[warn] duplex STT failed: {worker_err}")
                            else:
                                raise RuntimeError(f"duplex STT failed: {worker_err}")
                        now = time.monotonic()
                        if now - last_listening_hint_at >= 4.0:
                            print("[listening] Speak now.")
                            last_listening_hint_at = now
                        time.sleep(0.02)
                        continue
                    print(f"You(voice)> {user_text}")
                    last_listening_hint_at = 0.0
            else:
                user_text = input("You> ").strip()
        if not user_text:
            continue
        if user_text.lower() in {"/end", "/quit", "/exit"}:
            break
        if user_text.lower() == "/state":
            print(json.dumps(state, indent=2))
            continue

        last_user = user_text
        state["history"].append({"role": "user", "text": user_text})
        response = ""
        response_printed_live = False
        assistant_turn_active = bool(args.io_mode == "voice")
        if assistant_turn_active:
            agent_speaking_event.set()
        try:
            ingestion = llm_client.ingest_user_turn(state=state, user_text=user_text)
            memory_patch = ingestion.get("memory_patch", {})
            if isinstance(memory_patch, dict):
                _merge_memory_patch(state, memory_patch)
            signals = _normalize_llm_signals(
                fallback_signals=_blank_signals(),
                ingestion=ingestion,
            )
            state = _apply_llm_signals_to_state(
                state=state,
                signals=signals,
                policy=policy,
                ingestion=ingestion,
                allow_rule_fallback=False,
            )
            last_ingestion = ingestion
            if args.streaming:
                interrupt_event = threading.Event()
                chunks: List[str] = []
                print("Agent> ", end="", flush=True)
                barge_message = ""

                if args.io_mode == "voice" and voice_io is not None and tts_client is not None:
                    segment_queue: queue.Queue[str | None] = queue.Queue()
                    pcm_queue: queue.Queue[bytes | None] = queue.Queue()
                    segment_state: Dict[str, str] = {"buffer": ""}
                    tts_stop_event = threading.Event()
                    barge_stop_event = threading.Event()
                    barge_message_holder: Dict[str, str] = {}
                    min_tts_segment_chars_first = int(args.tts_segment_chars_first)
                    min_tts_segment_chars_next = int(args.tts_segment_chars_next)
                    first_segment_emitted = False

                    if args.enable_barge_in and duplex_worker is not None:
                        duplex_worker.clear_barge_text()
                        duplex_worker.clear_barge_signal()

                    def _tts_worker() -> None:
                        while True:
                            segment = segment_queue.get()
                            if segment is None:
                                pcm_queue.put(None)
                                return
                            if interrupt_event.is_set() or tts_stop_event.is_set():
                                pcm_queue.put(None)
                                return
                            try:
                                pcm = tts_client.synthesize_to_pcm(segment)
                            except Exception as exc:
                                print(f"\n[warn] TTS segment failed: {exc}")
                                continue
                            if interrupt_event.is_set() or tts_stop_event.is_set():
                                pcm_queue.put(None)
                                return
                            pcm_queue.put(pcm)

                    def _playback_worker() -> None:
                        voice_io.play_pcm_queue(
                            pcm_queue,
                            stop_event=interrupt_event,
                        )

                    def _barge_monitor() -> None:
                        if duplex_worker is None:
                            return
                        interrupted_local = False
                        wait_text_until = 0.0
                        while not barge_stop_event.is_set():
                            if not interrupted_local and duplex_worker.poll_barge_signal():
                                interrupt_event.set()
                                tts_stop_event.set()
                                interrupted_local = True
                                wait_text_until = time.monotonic() + 1.6
                            msg = duplex_worker.poll_barge_text().strip()
                            if msg:
                                barge_message_holder["text"] = msg
                                if not interrupted_local:
                                    interrupt_event.set()
                                    tts_stop_event.set()
                                return
                            if interrupted_local and time.monotonic() >= wait_text_until:
                                return
                            time.sleep(0.01)

                    tts_thread = threading.Thread(target=_tts_worker, daemon=True)
                    playback_thread = threading.Thread(target=_playback_worker, daemon=True)
                    barge_thread = None
                    tts_thread.start()
                    playback_thread.start()
                    if args.enable_barge_in:
                        barge_thread = threading.Thread(target=_barge_monitor, daemon=True)
                        barge_thread.start()

                    def _on_chunk(token: str) -> None:
                        nonlocal first_segment_emitted
                        if interrupt_event.is_set() or tts_stop_event.is_set():
                            return
                        chunks.append(token)
                        print(token, end="", flush=True)
                        segment_state["buffer"] += token
                        ready, remaining = _drain_speak_segments(segment_state["buffer"])
                        segment_state["buffer"] = remaining
                        pending = ""
                        for seg in ready:
                            merged = (pending + " " + seg).strip() if pending else seg
                            min_chars = (
                                min_tts_segment_chars_first
                                if not first_segment_emitted
                                else min_tts_segment_chars_next
                            )
                            if len(merged) < min_chars:
                                pending = merged
                                continue
                            segment_queue.put(merged)
                            first_segment_emitted = True
                            pending = ""
                        if pending:
                            segment_state["buffer"] = (pending + " " + segment_state["buffer"]).strip()

                    response, interrupted = llm_client.stream_generate_response_autonomous(
                        state=state,
                        user_text=user_text,
                        policy=policy,
                        script_pack=script_pack,
                        catalog=catalog,
                        on_text_chunk=_on_chunk,
                        stop_event=interrupt_event if args.enable_barge_in else None,
                    )

                    if interrupted:
                        # Fast-exit synthesis/playback threads after barge-in.
                        tts_stop_event.set()
                        segment_queue.put(None)
                        tts_thread.join(timeout=2.0)
                        playback_thread.join(timeout=2.0)
                    else:
                        tail_segments, tail_remaining = _drain_speak_segments(segment_state["buffer"])
                        for seg in tail_segments:
                            segment_queue.put(seg)
                        if tail_remaining.strip():
                            segment_queue.put(tail_remaining.strip())
                        segment_queue.put(None)
                        tts_thread.join(timeout=max(0.8, args.barge_max_seconds + 2.5))
                        playback_thread.join(timeout=max(0.8, args.barge_max_seconds + 2.5))
                    if barge_thread is not None:
                        barge_stop_event.set()
                        barge_thread.join(timeout=0.3)
                    if args.enable_barge_in and barge_message_holder.get("text"):
                        interrupted = True
                    if args.enable_barge_in and interrupt_event.is_set():
                        interrupted = True

                    if interrupted:
                        barge_message = barge_message_holder.get("text", "").strip()
                else:
                    monitor = None
                    if args.enable_barge_in:
                        monitor = BargeInMonitor(interrupt_event=interrupt_event, prefix=args.barge_prefix)
                        monitor.start()

                    def _on_chunk(token: str) -> None:
                        chunks.append(token)
                        print(token, end="", flush=True)

                    response, interrupted = llm_client.stream_generate_response_autonomous(
                        state=state,
                        user_text=user_text,
                        policy=policy,
                        script_pack=script_pack,
                        catalog=catalog,
                        on_text_chunk=_on_chunk,
                        stop_event=interrupt_event if args.enable_barge_in else None,
                    )

                    if monitor is not None:
                        monitor.stop()
                        barge_message = monitor.get_message()

                print("")
                response_printed_live = True

                last_stream_meta = {
                    "streaming_used": True,
                    "interrupted": bool(interrupted),
                    "tokens_streamed": len("".join(chunks)),
                    "io_mode": args.io_mode,
                }

                if interrupted and barge_message:
                    interruption_event = {
                        "event": "barge_in",
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                        "session_id": args.session_id,
                        "stage": state.get("stage", ""),
                        "io_mode": args.io_mode,
                        "partial_response": response,
                        "barge_text": barge_message,
                    }
                    interruption_events.append(interruption_event)
                    _append_jsonl(args.trace_log, interruption_event)

                    marked_response = (response + " [INTERRUPTED]").strip()
                    state["history"].append({"role": "agent", "text": marked_response})
                    print("[info] response interrupted; routing to new user turn.")
                    last_signals = signals
                    last_response = marked_response
                    pending_user_text = barge_message
                    continue
                if interrupted and not barge_message:
                    marked_response = (response + " [INTERRUPTED]").strip()
                    state["history"].append({"role": "agent", "text": marked_response})
                    print("[info] interruption detected but speech not recognized; please repeat.")
                    last_signals = signals
                    last_response = marked_response
                    continue

            else:
                response = llm_client.generate_response_autonomous(
                    state=state,
                    user_text=user_text,
                    policy=policy,
                    script_pack=script_pack,
                    catalog=catalog,
                )
                if args.io_mode == "voice" and tts_client is not None and voice_io is not None:
                    try:
                        pcm = tts_client.synthesize_to_pcm(response)
                        interrupt_event = threading.Event()
                        barge_stop_event = threading.Event()
                        barge_message_holder: Dict[str, str] = {}
                        pcm_queue: queue.Queue[bytes | None] = queue.Queue()
                        pcm_queue.put(pcm)
                        pcm_queue.put(None)

                        if args.enable_barge_in and duplex_worker is not None:
                            duplex_worker.clear_barge_text()
                            duplex_worker.clear_barge_signal()

                        def _barge_monitor() -> None:
                            if duplex_worker is None:
                                return
                            interrupted_local = False
                            wait_text_until = 0.0
                            while not barge_stop_event.is_set():
                                if not interrupted_local and duplex_worker.poll_barge_signal():
                                    interrupt_event.set()
                                    interrupted_local = True
                                    wait_text_until = time.monotonic() + 1.6
                                msg = duplex_worker.poll_barge_text().strip()
                                if msg:
                                    barge_message_holder["text"] = msg
                                    if not interrupted_local:
                                        interrupt_event.set()
                                    return
                                if interrupted_local and time.monotonic() >= wait_text_until:
                                    return
                                time.sleep(0.01)

                        playback_thread = threading.Thread(
                            target=voice_io.play_pcm_queue,
                            kwargs={"pcm_queue": pcm_queue, "stop_event": interrupt_event},
                            daemon=True,
                        )
                        barge_thread = None
                        playback_thread.start()
                        if args.enable_barge_in:
                            barge_thread = threading.Thread(target=_barge_monitor, daemon=True)
                            barge_thread.start()
                        playback_thread.join(timeout=max(0.8, args.barge_max_seconds + 2.5))
                        if barge_thread is not None:
                            barge_stop_event.set()
                            barge_thread.join(timeout=0.3)

                        if args.enable_barge_in and interrupt_event.is_set() and not barge_message_holder.get("text"):
                            # interruption happened but text may still be in-flight from STT worker.
                            time.sleep(0.25)
                            if duplex_worker is not None:
                                late_msg = duplex_worker.poll_barge_text().strip()
                                if late_msg:
                                    barge_message_holder["text"] = late_msg

                        barge_message = (
                            barge_message_holder.get("text", "").strip()
                            if args.enable_barge_in
                            else ""
                        )
                        if barge_message:
                            interruption_event = {
                                "event": "barge_in",
                                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                                "session_id": args.session_id,
                                "stage": state.get("stage", ""),
                                "io_mode": args.io_mode,
                                "partial_response": response,
                                "barge_text": barge_message,
                            }
                            interruption_events.append(interruption_event)
                            _append_jsonl(args.trace_log, interruption_event)
                            state["history"].append({"role": "agent", "text": (response + " [INTERRUPTED]").strip()})
                            last_signals = signals
                            last_response = response
                            pending_user_text = barge_message
                            continue
                    except Exception as exc:
                        print(f"[warn] voice playback failed: {exc}")
                last_stream_meta = {"streaming_used": False, "interrupted": False}
        except Exception as exc:
            if args.fallback_on_llm_error:
                signals = _blank_signals()
                last_ingestion = {"fallback": "llm_failed", "error": str(exc)}
                state["turn_index"] += 1
                response = "I hit a temporary issue. Could you repeat your last requirement in one sentence?"
                last_stream_meta = {"streaming_used": bool(args.streaming), "interrupted": False, "error": str(exc)}
                print(f"[warn] llm mode failure, used minimal safe fallback: {exc}")
            else:
                raise
        finally:
            if assistant_turn_active:
                agent_speaking_event.clear()

        state["history"].append({"role": "agent", "text": response})
        if not response_printed_live:
            print(f"Agent> {response}")

        last_signals = signals
        last_response = response

    if not state["history"] or not last_user:
        if duplex_worker is not None:
            duplex_worker.stop()
        if voice_io is not None:
            voice_io.close()
        raise RuntimeError("No conversation turns captured.")

    try:
        evaluation = llm_client.judge_live_call(
            session_id=args.session_id,
            iteration=str(policy.get("version", "v0")),
            state=state,
            transcript=state["history"],
            last_user_turn=last_user,
            last_response=last_response,
        )
    except Exception as exc:
        if args.fallback_on_llm_error:
            evaluation = _fallback_evaluation(
                session_id=args.session_id,
                iteration=str(policy.get("version", "v0")),
                state=state,
                last_user_turn=last_user,
                last_response=last_response,
                error=str(exc),
            )
            print(f"[warn] evaluation llm failed, used fallback evaluation: {exc}")
        else:
            raise

    session_payload = {
        "session_id": args.session_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "ingestion_mode": args.ingestion_mode,
        "policy_version": policy.get("version", "unknown"),
        "script_version": script_pack.get("version", "unknown"),
        "transcript": state["history"],
        "final_state": state,
        "evaluation": evaluation,
        "interruption_events": interruption_events,
        "last_artifacts": {
            "signals": last_signals,
            "ingestion": last_ingestion,
            "response": last_response,
            "streaming": last_stream_meta,
        },
    }

    out_file = args.out_dir / f"{args.session_id}.json"
    _save_json(out_file, session_payload)

    print("\nSession summary")
    print(f"- file: {out_file}")
    print(f"- outcome: {evaluation['outcome_label']}")
    print(f"- failure_tags: {evaluation['failure_tags']}")

    trace_event = {
        "event": "session_evaluation",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": args.session_id,
        "mode": args.mode,
        "ingestion_mode": args.ingestion_mode,
        "streaming": bool(args.streaming),
        "interruptions": len(interruption_events),
        "policy_version": policy.get("version", "unknown"),
        "script_version": script_pack.get("version", "unknown"),
        "outcome_label": evaluation.get("outcome_label", "unknown"),
        "failure_tags": evaluation.get("failure_tags", []),
        "session_file": str(out_file),
    }
    _append_jsonl(args.trace_log, trace_event)

    if voice_io is not None:
        if duplex_worker is not None:
            duplex_worker.stop()
        voice_io.close()

    return session_payload, policy, script_pack


def apply_self_improvement(
    *,
    args: argparse.Namespace,
    session_payload: Dict[str, Any],
    policy: Dict[str, Any],
    script_pack: Dict[str, Any],
) -> None:
    evaluation = session_payload.get("evaluation", {})
    failure_tags = [
        tag for tag in evaluation.get("failure_tags", [])
        if isinstance(tag, str) and tag.strip()
    ] if isinstance(evaluation, dict) else []
    outcome_label = str(evaluation.get("outcome_label", "unknown")) if isinstance(evaluation, dict) else "unknown"
    should_attempt_update = bool(failure_tags or outcome_label == "negative")

    live_report = _build_learning_report(
        session_id=session_payload["session_id"],
        policy_version=str(policy.get("version", "v0")),
        script_version=str(script_pack.get("version", "s0")),
        evaluation=session_payload["evaluation"],
    )

    llm_client = OpenAIChatClient(
        model=args.model,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        timeout_s=args.timeout_s,
        base_url=args.base_url,
    )

    updated_policy = policy
    updated_scripts = script_pack
    policy_changes: List[Dict[str, Any]] = []
    script_changes: List[Dict[str, Any]] = []
    policy_signals: Dict[str, Any] = {}
    script_signals: Dict[str, Any] = {}
    candidate_optimizer_errors: List[str] = []

    if should_attempt_update:
        for cycle in range(max(1, int(args.self_improve_cycles))):
            try:
                candidate_bundle = llm_client.propose_candidate_updates(
                    current_policy=updated_policy,
                    current_script_pack=updated_scripts,
                    live_evaluation=session_payload["evaluation"],
                    learning_report=live_report,
                    max_policy_changes=max(1, int(args.max_policy_changes)),
                    max_script_changes=max(1, int(args.max_script_changes)),
                )
            except Exception as exc:
                candidate_optimizer_errors.append(str(exc))
                break

            proposed_policy = candidate_bundle.get("candidate_policy", updated_policy)
            proposed_scripts = candidate_bundle.get("candidate_script_pack", updated_scripts)
            if not isinstance(proposed_policy, dict):
                proposed_policy = updated_policy
            if not isinstance(proposed_scripts, dict):
                proposed_scripts = updated_scripts

            (
                next_policy,
                next_scripts,
                policy_changed,
                script_changed,
            ) = _normalize_candidate_versions(
                current_policy=updated_policy,
                current_script_pack=updated_scripts,
                candidate_policy=proposed_policy,
                candidate_script_pack=proposed_scripts,
            )

            raw_policy_signals = candidate_bundle.get("policy_signals", {})
            raw_script_signals = candidate_bundle.get("script_signals", {})
            policy_signals = dict(raw_policy_signals) if isinstance(raw_policy_signals, dict) else {}
            script_signals = dict(raw_script_signals) if isinstance(raw_script_signals, dict) else {}

            cycle_policy_changes = candidate_bundle.get("policy_changes", [])
            if not isinstance(cycle_policy_changes, list):
                cycle_policy_changes = []
            cycle_script_changes = candidate_bundle.get("script_changes", [])
            if not isinstance(cycle_script_changes, list):
                cycle_script_changes = []

            if policy_changed and not cycle_policy_changes:
                cycle_policy_changes = [
                    {
                        "change_id": "llm_policy_update",
                        "apply": "LLM proposed a policy update.",
                        "expected_effect": "Address live call failures with minimal policy changes.",
                    }
                ]
            if script_changed and not cycle_script_changes:
                cycle_script_changes = [
                    {
                        "change_id": "llm_script_update",
                        "apply": "LLM proposed a script update.",
                        "expected_effect": "Improve response phrasing for observed failures.",
                    }
                ]

            if not policy_changed and not script_changed:
                break

            for change in cycle_policy_changes:
                if not isinstance(change, dict):
                    continue
                tracked = dict(change)
                tracked["cycle"] = cycle
                policy_changes.append(tracked)

            for change in cycle_script_changes:
                if not isinstance(change, dict):
                    continue
                tracked = dict(change)
                tracked["cycle"] = cycle
                script_changes.append(tracked)

            updated_policy = next_policy
            updated_scripts = next_scripts

    has_candidate_changes = bool(policy_changes or script_changes)

    gate_reasons: List[str] = []
    if not should_attempt_update:
        gate_reasons.append("session did not have negative outcome or failure tags")
    if should_attempt_update and not has_candidate_changes:
        gate_reasons.append("no policy/script changes were generated")
    if candidate_optimizer_errors:
        gate_reasons.append("llm candidate optimization failed")

    apply_policy_update = (
        should_attempt_update
        and has_candidate_changes
        and not candidate_optimizer_errors
        and bool(policy_changes)
    )
    apply_script_update = (
        should_attempt_update
        and has_candidate_changes
        and not candidate_optimizer_errors
        and bool(script_changes)
    )

    accepted_policy = updated_policy if apply_policy_update else policy
    accepted_scripts = updated_scripts if apply_script_update else script_pack
    accepted_candidate = bool(apply_policy_update or apply_script_update)
    gate_passed = accepted_candidate

    if apply_policy_update and args.write_back_policy:
        _save_json(args.policy, accepted_policy)
    if apply_script_update and args.write_back_scripts:
        _save_json(args.scripts, accepted_scripts)

    improve_payload = {
        "session_id": session_payload["session_id"],
        "trigger": {
            "outcome_label": outcome_label,
            "failure_tags": failure_tags,
            "attempted_update": should_attempt_update,
        },
        "from": {
            "policy_version": policy.get("version", "unknown"),
            "script_version": script_pack.get("version", "unknown"),
        },
        "candidate_to": {
            "policy_version": updated_policy.get("version", policy.get("version", "unknown")),
            "script_version": updated_scripts.get("version", script_pack.get("version", "unknown")),
        },
        "accepted_to": {
            "policy_version": accepted_policy.get("version", policy.get("version", "unknown")),
            "script_version": accepted_scripts.get("version", script_pack.get("version", "unknown")),
        },
        "accepted_candidate": accepted_candidate,
        "policy_changes": policy_changes,
        "script_changes": script_changes,
        "policy_signals": dict(policy_signals),
        "script_signals": dict(script_signals),
        "optimizer_errors": candidate_optimizer_errors,
        "validation": {
            "evaluation_mode": "no_gate_auto_apply",
            "learning_suite_skipped": True,
            "gate_passed": gate_passed,
            "gate_reasons": gate_reasons,
        },
        "write_back": {
            "policy": bool(apply_policy_update and args.write_back_policy),
            "scripts": bool(apply_script_update and args.write_back_scripts),
        },
    }

    improve_file = args.out_dir / f"{args.session_id}_improvement.json"
    _save_json(improve_file, improve_payload)

    trace_event = {
        "event": "self_improvement_gate",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "session_id": session_payload["session_id"],
        "from": improve_payload["from"],
        "accepted_to": improve_payload["accepted_to"],
        "accepted_candidate": improve_payload["accepted_candidate"],
        "gate_reasons": improve_payload["validation"]["gate_reasons"],
        "evaluation_mode": improve_payload["validation"]["evaluation_mode"],
        "improvement_file": str(improve_file),
    }
    _append_jsonl(args.trace_log, trace_event)

    print("\nSelf-improvement result")
    print(
        f"- versions (accepted): policy {improve_payload['from']['policy_version']} -> {improve_payload['accepted_to']['policy_version']}, "
        f"scripts {improve_payload['from']['script_version']} -> {improve_payload['accepted_to']['script_version']}"
    )
    _print_change_list("- policy changes", policy_changes)
    _print_change_list("- script changes", script_changes)
    print(f"- evaluation_mode: {improve_payload['validation']['evaluation_mode']}")
    print(f"- gate_passed: {gate_passed}")
    if gate_reasons:
        print(f"- gate_reasons: {gate_reasons}")
    print(f"- log file: {improve_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive sales call console (LLM + optional voice I/O)")
    parser.add_argument("--mode", choices=["llm"], default="llm")
    parser.add_argument(
        "--ingestion-mode",
        choices=["llm"],
        default="llm",
        help="User-turn ingestion mode.",
    )
    parser.add_argument("--policy", type=Path, default=Path("config/policy_version.yaml"))
    parser.add_argument("--scripts", type=Path, default=Path("assets/script_pack_v0.json"))
    parser.add_argument("--catalog", type=Path, default=Path("data/product_catalog.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("tests/live_calls"))
    parser.add_argument("--trace-log", type=Path, default=None)
    parser.add_argument("--session-id", type=str, default=None)

    parser.add_argument("--model", type=str, default="gpt-4.1-mini")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-output-tokens", type=int, default=220)
    parser.add_argument("--timeout-s", type=int, default=30)
    parser.add_argument("--base-url", type=str, default=None)
    parser.add_argument("--fallback-on-llm-error", action="store_true")

    parser.add_argument("--io-mode", choices=["text", "voice"], default="text")
    parser.add_argument("--voice-sample-rate", type=int, default=16000)
    parser.add_argument("--voice-frame-ms", type=int, default=20)
    parser.add_argument("--voice-vad-mode", type=int, default=2)
    parser.add_argument("--voice-energy-threshold", type=int, default=260)
    parser.add_argument("--voice-max-seconds", type=float, default=8.0)
    parser.add_argument("--voice-start-timeout-s", type=float, default=6.0)
    parser.add_argument("--voice-min-speech-ms", type=int, default=180)
    parser.add_argument("--voice-silence-ms", type=int, default=700)
    parser.add_argument("--voice-preroll-ms", type=int, default=220)
    parser.add_argument("--stt-model", type=str, default="gpt-4o-mini-transcribe")
    parser.add_argument("--stt-language", type=str, default="en")
    parser.add_argument("--tts-model", type=str, default="gpt-4o-mini-tts")
    parser.add_argument("--tts-voice", type=str, default="alloy")
    parser.add_argument("--tts-response-format", type=str, default="wav")
    parser.add_argument("--tts-speed", type=float, default=1.0)
    parser.add_argument("--tts-segment-chars-first", type=int, default=24)
    parser.add_argument("--tts-segment-chars-next", type=int, default=56)
    parser.add_argument(
        "--opening-greeting",
        type=str,
        default="Hi, this is Sayless. I can help you choose wireless earbuds. What will you mainly use them for?",
    )

    parser.add_argument("--streaming", dest="streaming", action="store_true")
    parser.add_argument("--no-streaming", dest="streaming", action="store_false")
    parser.add_argument("--enable-barge-in", dest="enable_barge_in", action="store_true")
    parser.add_argument("--disable-barge-in", dest="enable_barge_in", action="store_false")
    parser.add_argument("--barge-prefix", type=str, default="/barge ")
    parser.add_argument("--barge-max-seconds", type=float, default=6.0)
    parser.set_defaults(streaming=True, enable_barge_in=True)

    parser.add_argument("--self-improve", action="store_true")
    parser.add_argument("--self-improve-cycles", type=int, default=2)
    parser.add_argument("--max-policy-changes", type=int, default=1)
    parser.add_argument("--max-script-changes", type=int, default=1)
    parser.add_argument("--write-back-policy", action="store_true")
    parser.add_argument("--write-back-scripts", action="store_true")
    args = parser.parse_args()

    args.session_id = args.session_id or _next_session_id()
    if args.trace_log is None:
        args.trace_log = args.out_dir / "improvement_trace.jsonl"

    session_payload, policy, script_pack = run_live_session(args)

    if args.self_improve:
        apply_self_improvement(
            args=args,
            session_payload=session_payload,
            policy=policy,
            script_pack=script_pack,
        )


if __name__ == "__main__":
    main()
