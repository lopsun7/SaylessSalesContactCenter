#!/usr/bin/env python3
"""Deterministic policy optimizer for iteration-based self-improvement."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

from baseline_v0 import DEFAULT_POLICY, load_json_or_yaml, normalize_policy


def _next_version(version: str) -> str:
    if version.startswith("v") and version[1:].isdigit():
        return f"v{int(version[1:]) + 1}"
    return f"{version}.next"


def aggregate_failure_counts(report: Dict[str, Any]) -> Counter:
    counts: Counter = Counter()
    for result in report.get("results", []):
        for tag in result.get("evaluation", {}).get("failure_tags", []):
            counts[tag] += 1
    return counts


def optimize_policy(
    policy: Dict[str, Any],
    learning_report: Dict[str, Any],
    max_changes: int = 2,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Counter]:
    current = normalize_policy(policy)
    updated = deepcopy(current)
    rules = updated["rules"]
    counts = aggregate_failure_counts(learning_report)

    candidates: List[Dict[str, Any]] = []

    if counts["trust_not_addressed"] >= 1:
        candidates.append(
            {
                "change_id": "trust_unresolved_guard",
                "trigger": "trust_not_addressed >= 1",
                "target_layer": "content_planning",
                "apply": "trust_handling.always_include_if_unresolved = true",
                "expected_effect": "Reduce trust-related drop-offs",
            }
        )

    if counts["wrong_objection_handling"] >= 1:
        candidates.append(
            {
                "change_id": "price_objection_priority",
                "trigger": "wrong_objection_handling >= 1",
                "target_layer": "strategy_selection",
                "apply": "objection_priority puts price before authenticity",
                "expected_effect": "Address explicit price pushback earlier",
            }
        )

    if (counts["missed_need_discovery"] + counts["premature_closing"]) >= 2:
        candidates.append(
            {
                "change_id": "raise_close_gate",
                "trigger": "missed_need_discovery + premature_closing >= 2",
                "target_layer": "strategy_selection",
                "apply": "close_readiness.min_discovery_confidence += 0.1",
                "expected_effect": "Reduce premature close attempts",
            }
        )

    if counts["recommendation_mismatch"] >= 1:
        candidates.append(
            {
                "change_id": "increase_over_budget_penalty",
                "trigger": "recommendation_mismatch >= 1",
                "target_layer": "content_planning",
                "apply": "ranking_weights.over_budget_penalty += 1",
                "expected_effect": "Lower mismatch against budget constraints",
            }
        )

    if counts["information_overload"] >= 1:
        candidates.append(
            {
                "change_id": "tighten_response_length",
                "trigger": "information_overload >= 1",
                "target_layer": "response_generation",
                "apply": "response_limits.max_words_soft -= 10",
                "expected_effect": "Reduce verbosity-related disengagement",
            }
        )

    applied: List[Dict[str, Any]] = []
    for candidate in candidates:
        if len(applied) >= max_changes:
            break

        changed = False
        change_id = candidate["change_id"]

        if change_id == "trust_unresolved_guard":
            trust_cfg = rules.setdefault("trust_handling", {})
            if not trust_cfg.get("always_include_if_unresolved", False):
                trust_cfg["always_include_if_unresolved"] = True
                changed = True

        elif change_id == "price_objection_priority":
            priority = list(rules.get("objection_priority", []))
            target_order = ["price", "authenticity", "returns", "time", "brand_loyalty"]
            if priority != target_order:
                rules["objection_priority"] = target_order
                changed = True

        elif change_id == "raise_close_gate":
            close_cfg = rules.setdefault("close_readiness", {})
            old = float(close_cfg.get("min_discovery_confidence", 0.7))
            new = min(0.95, round(old + 0.1, 2))
            if new > old:
                close_cfg["min_discovery_confidence"] = new
                changed = True

        elif change_id == "increase_over_budget_penalty":
            rank_cfg = rules.setdefault("ranking_weights", {})
            old = int(rank_cfg.get("over_budget_penalty", 2))
            new = min(8, old + 1)
            if new > old:
                rank_cfg["over_budget_penalty"] = new
                changed = True

        elif change_id == "tighten_response_length":
            limits = rules.setdefault("response_limits", {})
            old = int(limits.get("max_words_soft", 80))
            new = max(40, old - 10)
            if new < old:
                limits["max_words_soft"] = new
                changed = True

        if changed:
            if change_id == "raise_close_gate":
                candidate["trigger_count"] = (
                    counts["missed_need_discovery"] + counts["premature_closing"]
                )
            elif change_id == "price_objection_priority":
                candidate["trigger_count"] = counts["wrong_objection_handling"]
            elif change_id == "trust_unresolved_guard":
                candidate["trigger_count"] = counts["trust_not_addressed"]
            elif change_id == "increase_over_budget_penalty":
                candidate["trigger_count"] = counts["recommendation_mismatch"]
            elif change_id == "tighten_response_length":
                candidate["trigger_count"] = counts["information_overload"]
            applied.append(candidate)

    if applied:
        updated["version"] = _next_version(str(current.get("version", "v0")))
        updated["updated_at"] = date.today().isoformat()

    return updated, applied, counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply deterministic policy updates from learning report")
    parser.add_argument("--policy", type=Path, default=Path("config/policy_version.yaml"))
    parser.add_argument("--report", type=Path, required=True, help="Learning run report JSON")
    parser.add_argument("--out-policy", type=Path, default=None, help="Path to write updated policy")
    parser.add_argument("--out-log", type=Path, default=Path("tests/policy_change_log.json"))
    parser.add_argument("--max-changes", type=int, default=2)
    args = parser.parse_args()

    policy = load_json_or_yaml(args.policy) if args.policy.exists() else deepcopy(DEFAULT_POLICY)
    report = json.loads(args.report.read_text(encoding="utf-8"))

    updated_policy, applied, failure_counts = optimize_policy(policy, report, args.max_changes)

    out_policy = args.out_policy or args.policy
    out_policy.parent.mkdir(parents=True, exist_ok=True)
    out_policy.write_text(json.dumps(updated_policy, indent=2), encoding="utf-8")

    log = {
        "from_version": policy.get("version", "unknown"),
        "to_version": updated_policy.get("version", "unknown"),
        "applied_changes": applied,
        "failure_counts": dict(failure_counts),
        "source_report": str(args.report),
        "date": date.today().isoformat(),
    }
    args.out_log.parent.mkdir(parents=True, exist_ok=True)
    args.out_log.write_text(json.dumps(log, indent=2), encoding="utf-8")

    print(f"Policy: {log['from_version']} -> {log['to_version']}")
    print(f"Applied changes: {len(applied)}")
    if applied:
        for item in applied:
            print(f"- {item['change_id']}: {item['apply']}")


if __name__ == "__main__":
    main()
