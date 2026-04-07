#!/usr/bin/env python3
"""Run iterative policy+script improvements and produce comparison artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

from baseline_v0 import (
    load_json_or_yaml,
    normalize_policy,
    normalize_script_pack,
    run_suite,
)
from policy_optimizer import optimize_policy
from script_optimizer import optimize_script_pack


def summarize_report(report: Dict[str, Any]) -> Dict[str, Any]:
    outcome_counts: Counter = Counter()
    failure_counts: Counter = Counter()
    root_kind_counts: Counter = Counter()

    for result in report.get("results", []):
        evaluation = result.get("evaluation", {})
        outcome_counts[evaluation.get("outcome_label", "unknown")] += 1
        root_kind_counts[evaluation.get("root_cause_kind", "none")] += 1
        for tag in evaluation.get("failure_tags", []):
            failure_counts[tag] += 1

    total = int(report.get("summary", {}).get("total", 0))
    passed = int(report.get("summary", {}).get("passed", 0))
    pass_rate = (passed / total) if total else 0.0

    return {
        "suite": report.get("suite", "unknown"),
        "iteration": report.get("iteration", "unknown"),
        "policy_version": report.get("policy_version", "unknown"),
        "script_version": report.get("script_version", "unknown"),
        "total": total,
        "passed": passed,
        "failed": int(report.get("summary", {}).get("failed", 0)),
        "pass_rate": round(pass_rate, 4),
        "outcomes": dict(outcome_counts),
        "failure_counts": dict(failure_counts),
        "root_cause_kind_counts": dict(root_kind_counts),
    }


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _delta(current: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    cur_fail = Counter(current.get("failure_counts", {}))
    base_fail = Counter(baseline.get("failure_counts", {}))
    keys = sorted(set(cur_fail.keys()) | set(base_fail.keys()))
    return {
        "pass_rate_delta": round(current.get("pass_rate", 0.0) - baseline.get("pass_rate", 0.0), 4),
        "passed_delta": int(current.get("passed", 0) - baseline.get("passed", 0)),
        "failed_delta": int(current.get("failed", 0) - baseline.get("failed", 0)),
        "failure_count_delta": {
            key: int(cur_fail.get(key, 0) - base_fail.get(key, 0))
            for key in keys
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run iterative policy+script improvements")
    parser.add_argument("--benchmark-cases", type=Path, default=Path("tests/executable_cases.yaml"))
    parser.add_argument("--learning-cases", type=Path, default=Path("tests/learning_calls.yaml"))
    parser.add_argument("--personas", type=Path, default=Path("data/test_personas.yaml"))
    parser.add_argument("--catalog", type=Path, default=Path("data/product_catalog.json"))
    parser.add_argument("--policy", type=Path, default=Path("config/policy_version.yaml"))
    parser.add_argument("--scripts", type=Path, default=Path("assets/script_pack_v0.json"))
    parser.add_argument("--outdir", type=Path, default=Path("tests/runs"))
    parser.add_argument("--cycles", type=int, default=2, help="Number of optimization cycles")
    parser.add_argument("--max-policy-changes", type=int, default=1)
    parser.add_argument("--max-script-changes", type=int, default=1)
    parser.add_argument("--write-back-policy", action="store_true")
    parser.add_argument("--write-back-scripts", action="store_true")
    args = parser.parse_args()

    current_policy = (
        normalize_policy(load_json_or_yaml(args.policy))
        if args.policy.exists()
        else normalize_policy({})
    )
    current_scripts = (
        normalize_script_pack(load_json_or_yaml(args.scripts))
        if args.scripts.exists()
        else normalize_script_pack({})
    )

    policies_dir = args.outdir / "policies"
    scripts_dir = args.outdir / "scripts"
    changes_log: List[Dict[str, Any]] = []
    version_summaries: List[Dict[str, Any]] = []

    for cycle in range(args.cycles + 1):
        iteration_label = f"iter_{cycle}"
        policy_version = str(current_policy.get("version", f"v{cycle}"))
        script_version = str(current_scripts.get("version", f"s{cycle}"))

        policy_snapshot_path = policies_dir / f"{policy_version}.yaml"
        script_snapshot_path = scripts_dir / f"{script_version}.json"
        _write_json(policy_snapshot_path, current_policy)
        _write_json(script_snapshot_path, current_scripts)

        benchmark_report = run_suite(
            cases_file=args.benchmark_cases,
            personas_file=args.personas,
            catalog_file=args.catalog,
            policy_file=policy_snapshot_path,
            script_file=script_snapshot_path,
            iteration=iteration_label,
        )
        learning_report = run_suite(
            cases_file=args.learning_cases,
            personas_file=args.personas,
            catalog_file=args.catalog,
            policy_file=policy_snapshot_path,
            script_file=script_snapshot_path,
            iteration=iteration_label,
        )

        _write_json(args.outdir / f"{iteration_label}_benchmark.json", benchmark_report)
        _write_json(args.outdir / f"{iteration_label}_learning.json", learning_report)

        version_summaries.append(
            {
                "iteration": iteration_label,
                "policy_version": policy_version,
                "script_version": script_version,
                "benchmark": summarize_report(benchmark_report),
                "learning": summarize_report(learning_report),
            }
        )

        if cycle == args.cycles:
            break

        updated_policy, policy_changes, policy_signals = optimize_policy(
            current_policy,
            learning_report,
            max_changes=args.max_policy_changes,
        )
        updated_scripts, script_changes, script_signals = optimize_script_pack(
            current_scripts,
            learning_report,
            max_changes=args.max_script_changes,
        )

        changes_log.append(
            {
                "cycle": cycle,
                "from": {
                    "policy_version": policy_version,
                    "script_version": script_version,
                },
                "to": {
                    "policy_version": updated_policy.get("version", policy_version),
                    "script_version": updated_scripts.get("version", script_version),
                },
                "policy_changes": policy_changes,
                "policy_signals": dict(policy_signals),
                "script_changes": script_changes,
                "script_signals": dict(script_signals),
            }
        )

        if not policy_changes and not script_changes:
            break

        current_policy = updated_policy
        current_scripts = updated_scripts

    baseline = version_summaries[0]
    final = version_summaries[-1]
    comparison = {
        "baseline_iteration": baseline["iteration"],
        "final_iteration": final["iteration"],
        "baseline_versions": {
            "policy_version": baseline["policy_version"],
            "script_version": baseline["script_version"],
        },
        "final_versions": {
            "policy_version": final["policy_version"],
            "script_version": final["script_version"],
        },
        "changes_log": changes_log,
        "iterations": version_summaries,
        "benchmark_delta_vs_baseline": _delta(final["benchmark"], baseline["benchmark"]),
        "learning_delta_vs_baseline": _delta(final["learning"], baseline["learning"]),
    }

    _write_json(args.outdir / "comparison.json", comparison)

    if args.write_back_policy:
        _write_json(args.policy, current_policy)
    if args.write_back_scripts:
        _write_json(args.scripts, current_scripts)

    print(f"Iterations completed: {len(version_summaries)}")
    print(
        "Versions: "
        f"policy {baseline['policy_version']} -> {final['policy_version']}, "
        f"scripts {baseline['script_version']} -> {final['script_version']}"
    )
    print(
        "Benchmark pass-rate delta: "
        f"{comparison['benchmark_delta_vs_baseline']['pass_rate_delta']:+.4f}"
    )
    print(
        "Learning pass-rate delta: "
        f"{comparison['learning_delta_vs_baseline']['pass_rate_delta']:+.4f}"
    )


if __name__ == "__main__":
    main()
