#!/usr/bin/env python3
"""Deterministic script/prompt assets optimizer."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

from baseline_v0 import DEFAULT_SCRIPT_PACK, load_json_or_yaml, normalize_script_pack


def _next_script_version(version: str) -> str:
    if version.startswith("s") and version[1:].isdigit():
        return f"s{int(version[1:]) + 1}"
    return f"{version}.next"


def aggregate_expression_signals(report: Dict[str, Any]) -> Dict[str, int]:
    counters: Counter = Counter()

    for result in report.get("results", []):
        evaluation = result.get("evaluation", {})
        reasons = result.get("reasons", [])

        for tag in evaluation.get("failure_tags", []):
            counters[f"tag:{tag}"] += 1

        for reason in reasons:
            reason_l = reason.lower()
            if "required keywords" in reason_l:
                counters["reason:missing_keywords"] += 1
            if "score[tone_control]" in reason_l:
                counters["reason:weak_tone_control"] += 1

    return dict(counters)


def optimize_script_pack(
    script_pack: Dict[str, Any],
    learning_report: Dict[str, Any],
    max_changes: int = 1,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, int]]:
    current = normalize_script_pack(script_pack)
    updated = deepcopy(current)
    templates = updated.setdefault("templates", {})
    snippets = updated.setdefault("snippets", {})
    style = updated.setdefault("style", {})

    signals = aggregate_expression_signals(learning_report)
    changes: List[Dict[str, Any]] = []

    candidates: List[Dict[str, Any]] = []

    if (
        (signals.get("reason:missing_keywords", 0) >= 1 and signals.get("tag:wrong_objection_handling", 0) >= 1)
        or signals.get("tag:wrong_objection_handling", 0) >= 1
    ):
        candidates.append(
            {
                "change_id": "price_template_clarity",
                "trigger": "wrong_objection_handling (and optional missing_keywords)",
                "target_layer": "response_generation",
                "apply": "strengthen price objection phrasing with lower/alternative/value terms",
                "expected_effect": "improve objection keyword coverage and clarity",
            }
        )

    if signals.get("tag:trust_not_addressed", 0) >= 1:
        candidates.append(
            {
                "change_id": "trust_snippet_strengthening",
                "trigger": "trust_not_addressed >= 1",
                "target_layer": "response_generation",
                "apply": "make trust lines explicit about authenticity, warranty, and returns",
                "expected_effect": "reduce trust-related expression misses",
            }
        )

    if signals.get("tag:information_overload", 0) >= 1 or signals.get("reason:weak_tone_control", 0) >= 1:
        candidates.append(
            {
                "change_id": "script_length_tightening",
                "trigger": "information_overload or weak_tone_control",
                "target_layer": "response_generation",
                "apply": "decrease script style max words",
                "expected_effect": "reduce verbose responses",
            }
        )

    for candidate in candidates:
        if len(changes) >= max_changes:
            break

        change_id = candidate["change_id"]
        changed = False

        if change_id == "price_template_clarity":
            new_alt = "For a lower-cost alternative, {name} (${price}) gives solid value without overpaying."
            new_fallback = "I can suggest a lower-cost alternative that keeps strong value."
            if templates.get("price_alternative") != new_alt or templates.get("price_fallback") != new_fallback:
                templates["price_alternative"] = new_alt
                templates["price_fallback"] = new_fallback
                changed = True
                candidate["trigger_count"] = int(
                    signals.get("reason:missing_keywords", 0)
                    + signals.get("tag:wrong_objection_handling", 0)
                )

        elif change_id == "trust_snippet_strengthening":
            new_auth = "These are authentic products, covered by a {warranty_months}-month warranty."
            new_ret = "You also have a clear {return_policy_days}-day return policy if the fit is not right."
            if snippets.get("trust_authentic") != new_auth or snippets.get("trust_return") != new_ret:
                snippets["trust_authentic"] = new_auth
                snippets["trust_return"] = new_ret
                changed = True
                candidate["trigger_count"] = int(signals.get("tag:trust_not_addressed", 0))

        elif change_id == "script_length_tightening":
            old_limit = int(style.get("max_words_soft", DEFAULT_SCRIPT_PACK["style"]["max_words_soft"]))
            new_limit = max(45, old_limit - 10)
            if new_limit < old_limit:
                style["max_words_soft"] = new_limit
                changed = True
                candidate["trigger_count"] = int(
                    signals.get("tag:information_overload", 0)
                    + signals.get("reason:weak_tone_control", 0)
                )

        if changed:
            changes.append(candidate)

    if changes:
        updated["version"] = _next_script_version(str(current.get("version", "s0")))
        updated["updated_at"] = date.today().isoformat()

    return updated, changes, signals


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply deterministic script asset updates from learning report")
    parser.add_argument("--scripts", type=Path, default=Path("assets/script_pack_v0.json"))
    parser.add_argument("--report", type=Path, required=True, help="Learning run report JSON")
    parser.add_argument("--out-scripts", type=Path, default=None)
    parser.add_argument("--out-log", type=Path, default=Path("tests/script_change_log.json"))
    parser.add_argument("--max-changes", type=int, default=1)
    args = parser.parse_args()

    script_pack = (
        load_json_or_yaml(args.scripts)
        if args.scripts.exists()
        else deepcopy(DEFAULT_SCRIPT_PACK)
    )
    learning_report = json.loads(args.report.read_text(encoding="utf-8"))

    updated, changes, signals = optimize_script_pack(
        script_pack,
        learning_report,
        max_changes=args.max_changes,
    )

    out_scripts = args.out_scripts or args.scripts
    out_scripts.parent.mkdir(parents=True, exist_ok=True)
    out_scripts.write_text(json.dumps(updated, indent=2), encoding="utf-8")

    log = {
        "from_version": script_pack.get("version", "unknown"),
        "to_version": updated.get("version", "unknown"),
        "applied_changes": changes,
        "signals": signals,
        "source_report": str(args.report),
        "date": date.today().isoformat(),
    }
    args.out_log.parent.mkdir(parents=True, exist_ok=True)
    args.out_log.write_text(json.dumps(log, indent=2), encoding="utf-8")

    print(f"Scripts: {log['from_version']} -> {log['to_version']}")
    print(f"Applied script changes: {len(changes)}")
    for change in changes:
        print(f"- {change['change_id']}: {change['apply']}")


if __name__ == "__main__":
    main()
