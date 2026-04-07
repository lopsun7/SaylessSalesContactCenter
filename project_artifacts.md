# Project Artifacts (Current Directory)

## Core Design Files
- `requirements.md`
- `failure_taxonomy.md`
- `test_matrix.md`
- `workflow_spec.md`
- `improvement_policy.md`

## Evaluation and Iteration Files
- `evaluation_rubric.md`
- `iteration_log.md`

## Suggested Next Non-Markdown Artifacts
- `product_catalog.json`
- `test_personas.yaml`
- `test_runs/` (directory for transcripts, labels, metrics by iteration)

## Recommended Implementation Order
1. Lock `requirements.md`.
2. Lock `failure_taxonomy.md`.
3. Freeze `test_matrix.md`.
4. Define strict I/O contracts from `workflow_spec.md`.
5. Encode rules from `improvement_policy.md`.
6. Implement baseline (Iteration 0) and evaluate using `evaluation_rubric.md`.
7. Run Iteration 1 and Iteration 2, recording evidence in `iteration_log.md`.

