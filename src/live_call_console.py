#!/usr/bin/env python3
"""Interactive console for live sales calls with optional self-improvement."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from baseline_v0 import (
    evaluate,
    extract_signals,
    generate_response,
    initial_state,
    load_json_or_yaml,
    load_policy,
    load_script_pack,
    plan_content,
    select_strategy,
    update_state,
)
from llm_module import OpenAIChatClient
from policy_optimizer import optimize_policy
from script_optimizer import optimize_script_pack

ALLOWED_USE_CASE = {"unknown", "commute", "calls", "gym", "running", "music", "work", "casual", "gaming"}
ALLOWED_BUDGET = {"unknown", "budget", "mid", "premium"}
ALLOWED_PRIORITY = {"unknown", "anc", "calls", "fit", "battery", "value", "sound", "reliability"}
ALLOWED_DEVICE = {"unknown", "ios", "android"}
ALLOWED_INTENT = {"explore", "compare", "buy", "defer", "reject", "unknown"}
ALLOWED_ENGAGEMENT = {"low", "medium", "high"}
ALLOWED_OBJECTIONS = {"price", "authenticity", "time", "returns", "brand_loyalty"}
ALLOWED_TRUST = {"authenticity", "warranty", "returns"}
ALLOWED_CONFLICTS = {"price_vs_quality", "feature_vs_budget"}


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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


def _next_session_id(prefix: str = "live") -> str:
    return f"{prefix}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _print_change_list(title: str, items: List[Dict[str, Any]]) -> None:
    print(title)
    if not items:
        print("- none")
        return
    for item in items:
        print(f"- {item.get('change_id', 'unknown')}: {item.get('apply', 'n/a')}")


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

    use_case = norm_slots.get("use_case")
    budget_tier = norm_slots.get("budget_tier")
    priority = norm_slots.get("priority")
    device = norm_slots.get("device")

    if isinstance(use_case, str) and use_case in ALLOWED_USE_CASE:
        normalized["constraints"]["use_case"] = use_case
    if isinstance(budget_tier, str) and budget_tier in ALLOWED_BUDGET:
        normalized["constraints"]["budget_tier"] = budget_tier
    if isinstance(priority, str) and priority in ALLOWED_PRIORITY:
        normalized["constraints"]["priority"] = priority
    if isinstance(device, str) and device in ALLOWED_DEVICE:
        normalized["constraints"]["device"] = device

    intent = signals.get("intent")
    if isinstance(intent, str) and intent in ALLOWED_INTENT:
        normalized["intent"] = intent

    engagement = signals.get("engagement")
    if isinstance(engagement, str) and engagement in ALLOWED_ENGAGEMENT:
        normalized["engagement"] = engagement

    objections = signals.get("objections", [])
    if isinstance(objections, list):
        normalized["objections"] = [x for x in objections if isinstance(x, str) and x in ALLOWED_OBJECTIONS]

    trust_flags = signals.get("trust_flags", [])
    if isinstance(trust_flags, list):
        normalized["trust_flags"] = [x for x in trust_flags if isinstance(x, str) and x in ALLOWED_TRUST]

    conflicts = signals.get("conflicts", [])
    if isinstance(conflicts, list):
        normalized["conflicts"] = [x for x in conflicts if isinstance(x, str) and x in ALLOWED_CONFLICTS]

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
            if isinstance(obj, str) and obj not in next_state["unresolved_objections"]:
                next_state["unresolved_objections"].append(obj)

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
    last_strategy: Dict[str, Any] = {}
    last_plan: Dict[str, Any] = {}
    last_response = ""
    last_user = ""
    last_ingestion: Dict[str, Any] = {}

    llm_client = None
    if args.mode == "llm" or args.ingestion_mode == "llm":
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

        strategy: Dict[str, Any]
        plan: Dict[str, Any]
        signals: Dict[str, Any]
        response = ""

        if args.mode == "llm":
            if llm_client is None:
                raise RuntimeError("llm mode requires an llm client.")
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

            strategy = {
                "goal": state.get("stage", "discovery"),
                "tactic": "llm_autonomous",
                "ask_clarification": False,
                "close_level": "none",
                "focus_points": [],
                "avoid": [],
            }
            plan = {
                "questions": [],
                "recommendations": [],
                "supporting_points": [],
                "trust_block": [],
                "cta": "",
            }
        else:
            fallback_signals = extract_signals(user_text, state)
            signals = fallback_signals

            if args.ingestion_mode == "llm":
                if llm_client is None:
                    raise RuntimeError("LLM ingestion requested but no llm client is available.")
                try:
                    ingestion = llm_client.ingest_user_turn(state=state, user_text=user_text)
                    memory_patch = ingestion.get("memory_patch", {})
                    if isinstance(memory_patch, dict):
                        _merge_memory_patch(state, memory_patch)
                    signals = _normalize_llm_signals(
                        fallback_signals=fallback_signals,
                        ingestion=ingestion,
                    )
                    last_ingestion = ingestion
                except Exception as exc:
                    if args.fallback_on_llm_error:
                        signals = fallback_signals
                        last_ingestion = {"fallback": "rule_based", "error": str(exc)}
                        print(f"[warn] ingestion llm failed, used rule fallback: {exc}")
                    else:
                        raise

            state = update_state(state, signals, user_text, policy)
            strategy = select_strategy(state, signals, policy)
            plan = plan_content(strategy, state, signals, catalog, policy, script_pack)

            if llm_client is not None:
                try:
                    response = llm_client.generate_response(
                        state=state,
                        strategy=strategy,
                        plan=plan,
                        policy=policy,
                        script_pack=script_pack,
                        user_text=user_text,
                        catalog=catalog,
                    )
                except Exception as exc:
                    if args.fallback_on_llm_error:
                        response = generate_response(plan, strategy, state, policy, script_pack)
                        print(f"[warn] llm failed, used deterministic fallback: {exc}")
                    else:
                        raise
            else:
                response = generate_response(plan, strategy, state, policy, script_pack)

        state["history"].append({"role": "agent", "text": response})
        print(f"Agent> {response}")

        last_signals = signals
        last_strategy = strategy
        last_plan = plan
        last_response = response

    if not state["history"] or not last_user:
        raise RuntimeError("No conversation turns captured.")

    evaluation = evaluate(
        test_id=args.session_id,
        iteration=str(policy.get("version", "v0")),
        state=state,
        last_signals=last_signals,
        last_strategy=last_strategy,
        last_plan=last_plan,
        last_response=last_response,
        last_user_turn=last_user,
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
            "strategy": last_strategy,
            "plan": last_plan,
            "response": last_response,
        },
    }

    out_file = args.out_dir / f"{args.session_id}.json"
    _save_json(out_file, session_payload)

    print("\nSession summary")
    print(f"- file: {out_file}")
    print(f"- outcome: {evaluation['outcome_label']}")
    print(f"- failure_tags: {evaluation['failure_tags']}")

    return session_payload, policy, script_pack


def apply_self_improvement(
    *,
    args: argparse.Namespace,
    session_payload: Dict[str, Any],
    policy: Dict[str, Any],
    script_pack: Dict[str, Any],
) -> None:
    learning_report = _build_learning_report(
        session_id=session_payload["session_id"],
        policy_version=str(policy.get("version", "v0")),
        script_version=str(script_pack.get("version", "s0")),
        evaluation=session_payload["evaluation"],
    )

    updated_policy, policy_changes, policy_signals = optimize_policy(
        policy,
        learning_report,
        max_changes=args.max_policy_changes,
    )
    updated_scripts, script_changes, script_signals = optimize_script_pack(
        script_pack,
        learning_report,
        max_changes=args.max_script_changes,
    )

    if policy_changes and args.write_back_policy:
        _save_json(args.policy, updated_policy)
    if script_changes and args.write_back_scripts:
        _save_json(args.scripts, updated_scripts)

    improve_payload = {
        "session_id": session_payload["session_id"],
        "from": {
            "policy_version": policy.get("version", "unknown"),
            "script_version": script_pack.get("version", "unknown"),
        },
        "to": {
            "policy_version": updated_policy.get("version", policy.get("version", "unknown")),
            "script_version": updated_scripts.get("version", script_pack.get("version", "unknown")),
        },
        "policy_changes": policy_changes,
        "script_changes": script_changes,
        "policy_signals": dict(policy_signals),
        "script_signals": dict(script_signals),
        "write_back": {
            "policy": bool(policy_changes and args.write_back_policy),
            "scripts": bool(script_changes and args.write_back_scripts),
        },
    }

    improve_file = args.out_dir / f"{args.session_id}_improvement.json"
    _save_json(improve_file, improve_payload)

    print("\nSelf-improvement result")
    print(
        f"- versions: policy {improve_payload['from']['policy_version']} -> {improve_payload['to']['policy_version']}, "
        f"scripts {improve_payload['from']['script_version']} -> {improve_payload['to']['script_version']}"
    )
    _print_change_list("- policy changes", policy_changes)
    _print_change_list("- script changes", script_changes)
    print(f"- log file: {improve_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive sales call console (deterministic/llm)")
    parser.add_argument("--mode", choices=["deterministic", "llm"], default="llm")
    parser.add_argument(
        "--ingestion-mode",
        choices=["auto", "rule", "llm"],
        default="auto",
        help="How user-turn ingestion is done before strategy/planning.",
    )
    parser.add_argument("--policy", type=Path, default=Path("config/policy_version.yaml"))
    parser.add_argument("--scripts", type=Path, default=Path("assets/script_pack_v0.json"))
    parser.add_argument("--catalog", type=Path, default=Path("data/product_catalog.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("tests/live_calls"))
    parser.add_argument("--session-id", type=str, default=None)

    parser.add_argument("--model", type=str, default="gpt-4.1-mini")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-output-tokens", type=int, default=220)
    parser.add_argument("--timeout-s", type=int, default=30)
    parser.add_argument("--base-url", type=str, default=None)
    parser.add_argument("--fallback-on-llm-error", action="store_true")

    parser.add_argument("--self-improve", action="store_true")
    parser.add_argument("--max-policy-changes", type=int, default=1)
    parser.add_argument("--max-script-changes", type=int, default=1)
    parser.add_argument("--write-back-policy", action="store_true")
    parser.add_argument("--write-back-scripts", action="store_true")
    args = parser.parse_args()

    args.session_id = args.session_id or _next_session_id()
    if args.ingestion_mode == "auto":
        args.ingestion_mode = "llm" if args.mode == "llm" else "rule"

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
