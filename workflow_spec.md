# Workflow Spec: Response Generation and Improvement Loop

## Design Principle
Improvements must target reusable workflow layers, not one monolithic prompt rewrite.

## Strict Module Contracts

| Module | Input Contract | Output Contract | Schema File |
|---|---|---|---|
| `state_tracker` | previous `ConversationState` + latest user turn text | updated `ConversationState` | `schemas/state.schema.json` |
| `signal_extractor` | user turn text + `ConversationState` | `UserSignals` | `schemas/signals.schema.json` |
| `strategy_selector` | `ConversationState` + `UserSignals` + `policy_version.yaml` | `TurnStrategy` | `schemas/strategy.schema.json` |
| `content_planner` | `TurnStrategy` + `ConversationState` + `UserSignals` + `product_catalog.json` | `ContentPlan` | `schemas/content_plan.schema.json` |
| `response_generator` | `ContentPlan` + `TurnStrategy` | `AgentResponse` | `schemas/response.schema.json` |
| `evaluator` | full transcript + final state + final strategy/plan/response | `EvaluationResult` | `schemas/evaluation.schema.json` |

## Layered Workflow (Executable)

### 1) `state_tracker`
- Input:
  - previous state object,
  - current user utterance,
  - extracted constraints/objections from current turn.
- Output:
  - state with updated slots and stage:
    - `discovery | recommendation | objection_handling | closing`.
- Responsibility:
  - Canonical memory and turn-to-turn consistency.
- Improvement surface:
  - slot overwrite policy, stage transition rules.

### 2) `signal_extractor`
- Input:
  - current user utterance,
  - optional state context.
- Output:
  - normalized intent, objections, trust flags, ambiguity/conflict, confidence.
- Responsibility:
  - deterministic label extraction for downstream modules.
- Improvement surface:
  - keyword maps, confidence thresholds, ambiguity logic.

### 3) `strategy_selector`
- Input:
  - state + signals + current policy version.
- Output:
  - tactic selection and close level.
- Responsibility:
  - chooses action type before language generation.
- Improvement surface:
  - objection precedence, close gating, fallback behavior.

### 4) `content_planner`
- Input:
  - strategy + state + signals + product catalog.
- Output:
  - question list, recommendations, support points, trust block, CTA.
- Responsibility:
  - factual content planning and ordering.
- Improvement surface:
  - recommendation ranking, trust block injection, verbosity shaping.

### 5) `response_generator`
- Input:
  - content plan + strategy.
- Output:
  - final text response with soft length constraints.
- Responsibility:
  - concise and policy-aligned response realization.
- Improvement surface:
  - template tuning for tone and compression.

### 6) `evaluator`
- Input:
  - transcript + final artifacts (state/signals/strategy/plan/response).
- Output:
  - rubric scores, failure tags, root cause layer, outcome label.
- Responsibility:
  - consistent grading for iteration comparison.
- Improvement surface:
  - tag attribution precision, score threshold calibration.

### 7) `policy_optimizer` (next step, not in baseline runtime)
- Input:
  - historical evaluation outputs.
- Output:
  - `policy_version.yaml` delta with changelog.
- Responsibility:
  - bounded and traceable policy updates.
- Improvement surface:
  - trigger thresholds and rollback conditions.

## Concrete Files in This Repo
- Catalog: `data/product_catalog.json`
- Personas: `data/test_personas.yaml`
- Policy version: `config/policy_version.yaml`
- Executable cases (8): `tests/executable_cases.yaml`
- Baseline pipeline: `src/baseline_v0.py`

## Guardrails
- No hallucinated product claims outside catalog.
- No close attempt if critical discovery slots are missing.
- If user disengagement rises, shorten and simplify response.
