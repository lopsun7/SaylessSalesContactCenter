# Self-Improving Call Center Sales Agent (Binox G1)

This project is a text-first simulation of a sales call center agent for wireless earbuds.

It demonstrates:
- sales conversation handling,
- outcome -> analysis -> targeted improvement loop,
- explicit improvement logic,
- at least 2 iteration cycles with evidence.

## Scope
- Domain: wireless earbuds e-commerce recommendation/sales.
- Input mode: text-first (voice-ready architecture, voice not implemented here).
- Design goal: modular, diagnosable improvements (not monolithic prompt rewrites).

## Core Architecture
Pipeline:
1. `state_tracker`
2. `signal_extractor`
3. `strategy_selector`
4. `content_planner`
5. `response_generator`
6. `evaluator`

Main runtime: `src/baseline_v0.py`
Interactive runtime: `src/live_call_console.py`

## Two Improvement Channels
### 1) Policy Improvement (`what to do`)
Stable behavior rules (decision layer), for example:
- discovery gates,
- objection ordering,
- close readiness,
- ranking weights,
- response limits.

Config: `config/policy_version.yaml`
Optimizer: `src/policy_optimizer.py`

### 2) Script Improvement (`how to say it`)
Language realization assets (expression layer), for example:
- objection templates,
- trust snippets,
- soft-close phrasing,
- style constraints.

Config: `assets/script_pack_v0.json`
Optimizer: `src/script_optimizer.py`

## Test Strategy
- Fixed benchmark suite (regression guard): `tests/executable_cases.yaml`
- Learning replay suite (drives updates): `tests/learning_calls.yaml`

Iteration orchestrator: `src/run_iterations.py`
Report generator: `src/generate_iteration_report.py`

## Live LLM Interaction (OpenAI API)
You can chat with the agent in real time and optionally apply self-improvement from the live call.

Set API key:
```bash
export OPENAI_API_KEY=\"<your_key>\"
```

Start interactive LLM chat:
```bash
make chat
```

Chat + apply self-improvement (writes back to policy/scripts):
```bash
make chat-improve
```

Notes:
- Live session artifacts are saved under `tests/live_calls/`.
- In `--mode llm`, ingestion and reply generation are fully LLM-driven (no rule pipeline for turn handling).
- If API call fails and fallback is enabled in `--mode llm`, the app sends a minimal safe retry prompt.
- In deterministic mode, fallback behavior remains rule-based.
- Model can be changed with `OPENAI_MODEL` (default currently `gpt-4.1-mini`).

Direct run examples:
```bash
python3 src/live_call_console.py --mode llm --fallback-on-llm-error
python3 src/live_call_console.py --mode deterministic --ingestion-mode rule
```

## One-Command Demo
Requirements:
- `python3`
- `make`

Run:
```bash
make demo
```

This executes:
- baseline benchmark run,
- iterative optimization (`iter_0 -> iter_1 -> iter_2`),
- report generation.

## Key Output Artifacts
- Iteration comparison JSON: `tests/runs/comparison.json`
- Human-readable report: `docs/iteration_report.md`
- Iteration log: `iteration_log.md`

## Current Evidence Snapshot
From the latest run:
- Benchmark pass rate: `100%` throughout (no regression)
- Learning pass rate: `66.67% -> 83.33% -> 100%`
- Versions: `policy v0 -> v2`, `scripts s0 -> s1`

## Supporting Design Docs
- `requirements.md`
- `failure_taxonomy.md`
- `test_matrix.md`
- `workflow_spec.md`
- `improvement_policy.md`
- `evaluation_rubric.md`

## Notes
- This repository intentionally prioritizes explainability and reproducibility for take-home assessment evaluation.
- No external LLM API is required for this deterministic baseline simulation.
