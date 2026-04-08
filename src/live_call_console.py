#!/usr/bin/env python3
"""Interactive console for live sales calls with optional self-improvement."""

from __future__ import annotations

import argparse
import json
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

    print("Interactive call started.")
    print("Type your message. Use '/end' to finish, '/state' to inspect state.")

    while True:
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
            response = llm_client.generate_response_autonomous(
                state=state,
                user_text=user_text,
                policy=policy,
                script_pack=script_pack,
                catalog=catalog,
            )
        except Exception as exc:
            if args.fallback_on_llm_error:
                signals = _blank_signals()
                last_ingestion = {"fallback": "llm_failed", "error": str(exc)}
                state["turn_index"] += 1
                response = "I hit a temporary issue. Could you repeat your last requirement in one sentence?"
                print(f"[warn] llm mode failure, used minimal safe fallback: {exc}")
            else:
                raise

        state["history"].append({"role": "agent", "text": response})
        print(f"Agent> {response}")

        last_signals = signals
        last_response = response

    if not state["history"] or not last_user:
        raise RuntimeError("No conversation turns captured.")

    evaluation = llm_client.judge_live_call(
        session_id=args.session_id,
        iteration=str(policy.get("version", "v0")),
        state=state,
        transcript=state["history"],
        last_user_turn=last_user,
        last_response=last_response,
    )

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
        "last_artifacts": {
            "signals": last_signals,
            "ingestion": last_ingestion,
            "response": last_response,
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
        "policy_version": policy.get("version", "unknown"),
        "script_version": script_pack.get("version", "unknown"),
        "outcome_label": evaluation.get("outcome_label", "unknown"),
        "failure_tags": evaluation.get("failure_tags", []),
        "session_file": str(out_file),
    }
    _append_jsonl(args.trace_log, trace_event)

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
    llm_gate_errors: List[str] = []
    llm_gate: Dict[str, Any] = {
        "apply_candidate": False,
        "apply_policy": False,
        "apply_script_pack": False,
        "confidence": 0.0,
        "reasons": [],
        "risk_flags": [],
        "notes": "",
    }

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
    if should_attempt_update and has_candidate_changes and not candidate_optimizer_errors:
        try:
            llm_gate = llm_client.judge_candidate_updates(
                live_evaluation=session_payload["evaluation"],
                current_policy=policy,
                current_script_pack=script_pack,
                candidate_policy=updated_policy,
                candidate_script_pack=updated_scripts,
                policy_changes=policy_changes,
                script_changes=script_changes,
            )
        except Exception as exc:
            llm_gate_errors.append(str(exc))

    gate_reasons: List[str] = []
    if not should_attempt_update:
        gate_reasons.append("session did not have negative outcome or failure tags")
    if should_attempt_update and not has_candidate_changes:
        gate_reasons.append("no policy/script changes were generated")
    if candidate_optimizer_errors:
        gate_reasons.append("llm candidate optimization failed")
    if llm_gate_errors:
        gate_reasons.append("llm gate judgment failed")

    gate_apply_candidate = bool(llm_gate.get("apply_candidate"))
    gate_apply_policy = bool(llm_gate.get("apply_policy"))
    gate_apply_script_pack = bool(llm_gate.get("apply_script_pack"))
    if should_attempt_update and has_candidate_changes and not llm_gate_errors and not gate_apply_candidate:
        gate_reasons.append("llm gate rejected candidate")
        model_reasons = llm_gate.get("reasons", [])
        if isinstance(model_reasons, list):
            for reason in model_reasons[:3]:
                if isinstance(reason, str) and reason.strip():
                    gate_reasons.append(f"llm_gate: {reason.strip()}")
    if should_attempt_update and has_candidate_changes and gate_apply_candidate and not (gate_apply_policy or gate_apply_script_pack):
        gate_reasons.append("llm gate accepted candidate but selected no component to apply")

    apply_policy_update = (
        should_attempt_update
        and has_candidate_changes
        and not candidate_optimizer_errors
        and not llm_gate_errors
        and gate_apply_candidate
        and gate_apply_policy
        and bool(policy_changes)
    )
    apply_script_update = (
        should_attempt_update
        and has_candidate_changes
        and not candidate_optimizer_errors
        and not llm_gate_errors
        and gate_apply_candidate
        and gate_apply_script_pack
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
        "llm_gate": llm_gate,
        "llm_gate_errors": llm_gate_errors,
        "validation": {
            "evaluation_mode": "llm_gate_only",
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
        "llm_gate": improve_payload["llm_gate"],
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
    print(
        f"- llm_gate: apply_candidate={gate_apply_candidate}, "
        f"apply_policy={gate_apply_policy}, apply_script_pack={gate_apply_script_pack}, "
        f"confidence={llm_gate.get('confidence', 0.0)}"
    )
    print(f"- gate_passed: {gate_passed}")
    if gate_reasons:
        print(f"- gate_reasons: {gate_reasons}")
    print(f"- log file: {improve_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive sales call console (llm only)")
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
