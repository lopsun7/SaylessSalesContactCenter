# Iteration Report

- Baseline iteration: `iter_0`
- Final iteration: `iter_2`
- Policy versions: `v0` -> `v2`
- Script versions: `s0` -> `s1`

## Iteration Metrics

| Iteration | Policy | Scripts | Benchmark Pass | Learning Pass | Learning Failures |
|---|---|---|---:|---:|---|
| iter_0 | v0 | s0 | 9/9 (100.00%) | 4/6 (66.67%) | trust_not_addressed:1, wrong_objection_handling:2 |
| iter_1 | v1 | s1 | 9/9 (100.00%) | 5/6 (83.33%) | wrong_objection_handling:2 |
| iter_2 | v2 | s1 | 9/9 (100.00%) | 6/6 (100.00%) | none |

## Applied Changes

### Cycle 0 (policy `v0` -> `v1`, scripts `s0` -> `s1`)
- Policy changes:
  - `trust_unresolved_guard` (content_planning): trust_handling.always_include_if_unresolved = true [trigger: trust_not_addressed >= 1, count=1]
- Script changes:
  - `price_template_clarity` (response_generation): strengthen price objection phrasing with lower/alternative/value terms [trigger: wrong_objection_handling (and optional missing_keywords), count=4]

### Cycle 1 (policy `v1` -> `v2`, scripts `s1` -> `s1`)
- Policy changes:
  - `price_objection_priority` (strategy_selection): objection_priority puts price before authenticity [trigger: wrong_objection_handling >= 1, count=2]
- Script changes: none

## Delta vs Baseline

### Benchmark
- Pass-rate delta: +0.0000
- Passed delta: +0
- Failed delta: +0
- none

### Learning
- Pass-rate delta: +0.3333
- Passed delta: +2
- Failed delta: -2
- trust_not_addressed: -1
- wrong_objection_handling: -2

## Interpretation
- `Policy` updates adjust behavior rules (what the agent does).
- `Script` updates adjust wording assets (how the agent says it).
