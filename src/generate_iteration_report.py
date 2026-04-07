#!/usr/bin/env python3
"""Generate markdown summary from iteration comparison JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict


def _fmt_failures(failures: Dict[str, int]) -> str:
    if not failures:
        return "- none"
    lines = []
    for key, value in sorted(failures.items(), key=lambda item: (item[1], item[0]), reverse=True):
        if value == 0:
            continue
        lines.append(f"- {key}: {value:+d}")
    return "\n".join(lines) if lines else "- none"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate iteration_report.md")
    parser.add_argument("--comparison", type=Path, default=Path("tests/runs/comparison.json"))
    parser.add_argument("--out", type=Path, default=Path("docs/iteration_report.md"))
    args = parser.parse_args()

    comparison = json.loads(args.comparison.read_text(encoding="utf-8"))
    iterations = comparison.get("iterations", [])
    changes = comparison.get("changes_log", [])

    lines = []
    lines.append("# Iteration Report")
    lines.append("")
    lines.append(f"- Baseline iteration: `{comparison.get('baseline_iteration', 'unknown')}`")
    lines.append(f"- Final iteration: `{comparison.get('final_iteration', 'unknown')}`")
    base_versions = comparison.get("baseline_versions", {})
    final_versions = comparison.get("final_versions", {})
    lines.append(
        f"- Policy versions: `{base_versions.get('policy_version', 'unknown')}` -> `{final_versions.get('policy_version', 'unknown')}`"
    )
    lines.append(
        f"- Script versions: `{base_versions.get('script_version', 'unknown')}` -> `{final_versions.get('script_version', 'unknown')}`"
    )
    lines.append("")

    lines.append("## Iteration Metrics")
    lines.append("")
    lines.append("| Iteration | Policy | Scripts | Benchmark Pass | Learning Pass | Learning Failures |")
    lines.append("|---|---|---|---:|---:|---|")
    for item in iterations:
        b = item["benchmark"]
        l = item["learning"]
        l_fail = ", ".join(f"{k}:{v}" for k, v in sorted(l.get("failure_counts", {}).items())) or "none"
        lines.append(
            "| "
            f"{item['iteration']} | {item['policy_version']} | {item['script_version']} | "
            f"{b['passed']}/{b['total']} ({b['pass_rate']:.2%}) | "
            f"{l['passed']}/{l['total']} ({l['pass_rate']:.2%}) | {l_fail} |"
        )

    lines.append("")
    lines.append("## Applied Changes")
    lines.append("")
    if not changes:
        lines.append("- No changes were applied.")
    else:
        for c in changes:
            lines.append(
                f"### Cycle {c['cycle']} "
                f"(policy `{c['from']['policy_version']}` -> `{c['to']['policy_version']}`, "
                f"scripts `{c['from']['script_version']}` -> `{c['to']['script_version']}`)"
            )
            if c.get("policy_changes"):
                lines.append("- Policy changes:")
                for p in c["policy_changes"]:
                    lines.append(
                        f"  - `{p['change_id']}` ({p['target_layer']}): {p['apply']} "
                        f"[trigger: {p['trigger']}, count={p.get('trigger_count', 0)}]"
                    )
            else:
                lines.append("- Policy changes: none")

            if c.get("script_changes"):
                lines.append("- Script changes:")
                for s in c["script_changes"]:
                    lines.append(
                        f"  - `{s['change_id']}` ({s['target_layer']}): {s['apply']} "
                        f"[trigger: {s['trigger']}, count={s.get('trigger_count', 0)}]"
                    )
            else:
                lines.append("- Script changes: none")
            lines.append("")

    lines.append("## Delta vs Baseline")
    lines.append("")
    b_delta = comparison.get("benchmark_delta_vs_baseline", {})
    l_delta = comparison.get("learning_delta_vs_baseline", {})
    lines.append("### Benchmark")
    lines.append(f"- Pass-rate delta: {b_delta.get('pass_rate_delta', 0):+.4f}")
    lines.append(f"- Passed delta: {b_delta.get('passed_delta', 0):+d}")
    lines.append(f"- Failed delta: {b_delta.get('failed_delta', 0):+d}")
    lines.append(_fmt_failures(b_delta.get("failure_count_delta", {})))
    lines.append("")
    lines.append("### Learning")
    lines.append(f"- Pass-rate delta: {l_delta.get('pass_rate_delta', 0):+.4f}")
    lines.append(f"- Passed delta: {l_delta.get('passed_delta', 0):+d}")
    lines.append(f"- Failed delta: {l_delta.get('failed_delta', 0):+d}")
    lines.append(_fmt_failures(l_delta.get("failure_count_delta", {})))
    lines.append("")
    lines.append("## Interpretation")
    lines.append("- `Policy` updates adjust behavior rules (what the agent does).")
    lines.append("- `Script` updates adjust wording assets (how the agent says it).")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
