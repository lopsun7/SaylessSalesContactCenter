# Iteration Log

## Goal
Track both behavior-rule changes (`policy`) and language-asset changes (`scripts`) with measurable impact and no benchmark regression.

## Iteration 0 (Baseline)
- Iteration: `iter_0`
- Policy version: `v0`
- Script version: `s0`
- Run date: 2026-04-07
- Benchmark results: 8/8 (100.00%), outcomes positive=6 neutral=2 negative=0
- Learning replay results: 4/6 (66.67%), outcomes positive=3 neutral=2 negative=1
- Top failure tags (learning):
  - `wrong_objection_handling`: 2
  - `trust_not_addressed`: 1

## Iteration 1
- Iteration: `iter_1`
- Policy version: `v1`
- Script version: `s1`
- Run date: 2026-04-07
- Changes applied:
  - Policy change `trust_unresolved_guard`: `trust_handling.always_include_if_unresolved = true`
  - Script change `price_template_clarity`: strengthened price objection phrasing with explicit lower/alternative/value terms
- Results vs Iteration 0:
  - Benchmark: 8/8 -> 8/8 (no regression)
  - Learning: 4/6 -> 5/6 (+16.66pp)
  - Failure-tag deltas: `trust_not_addressed` 1 -> 0, `wrong_objection_handling` 2 -> 2
- Decision: Keep.

## Iteration 2
- Iteration: `iter_2`
- Policy version: `v2`
- Script version: `s1`
- Run date: 2026-04-07
- Changes applied:
  - Policy change `price_objection_priority`: `objection_priority = [price, authenticity, returns, time, brand_loyalty]`
  - Script changes: none
- Results vs Iteration 1:
  - Benchmark: 8/8 -> 8/8 (no regression)
  - Learning: 5/6 -> 6/6 (+16.67pp)
  - Failure-tag deltas: `wrong_objection_handling` 2 -> 0
- Decision: Keep.

## Final Takeaways
- Policy improvements changed decision behavior (what to do): trust guard + objection ordering.
- Script improvements changed expression behavior (how to say it): clearer price objection template wording.
- Net improvement:
  - Learning pass rate 66.67% -> 100.00%
  - Benchmark pass rate 100.00% -> 100.00% (no regression)
