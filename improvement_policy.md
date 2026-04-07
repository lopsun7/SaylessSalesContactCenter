# Improvement Policy: Outcome -> Analysis -> Targeted Update

## Objective
Define deterministic, explainable rules for converting repeated failures into small, auditable behavior updates.

## Two Improvement Channels
- Policy improvement:
  - updates stable behavior rules (`what to do`), such as close gates, objection order, ranking weights.
- Script improvement:
  - updates language assets (`how to say it`), such as objection templates, trust snippets, concise phrasing.

## Policy Unit
Each policy change should include:
- `policy_id`
- `version_from`
- `version_to`
- `trigger_failure_type`
- `trigger_threshold`
- `target_layer`
- `change_description`
- `expected_effect`
- `affected_test_ids`
- `rollback_condition`

## Update Rules

### Rule 1: Missed Need Discovery
- Trigger:
  - `Missed Need Discovery` in >= 3 calls within last batch of 10.
- Target layer:
  - User Signal Extraction + Conversation State Tracking.
- Change:
  - Enforce minimum discovery slots before recommendation:
    - use-case,
    - budget,
    - primary priority.
- Expected effect:
  - Fewer premature recommendations and mismatches.

### Rule 2: Price Objection Mishandling
- Trigger:
  - `Wrong Objection Handling` with price objections in >= 2 calls.
- Target layer:
  - Strategy Selection + Content Planning.
- Change:
  - Add mandatory "value framing + lower-tier alternative + tradeoff summary" block.
- Expected effect:
  - Better objection recovery and neutral/positive outcome shift.

### Rule 3: Trust Not Addressed
- Trigger:
  - `Trust Not Addressed` in any call labeled negative.
- Target layer:
  - Signal Extraction + Content Planning.
- Change:
  - Prioritize authenticity/warranty/return response before additional selling.
- Expected effect:
  - Reduced trust-related drop-off.

### Rule 4: Information Overload
- Trigger:
  - `Information Overload` in >= 2 calls or low-engagement pattern detected.
- Target layer:
  - Content Planning + Final Response Generation.
- Change:
  - Cap response content to top 2 points + 1 question for next turn.
- Expected effect:
  - Better engagement and turn continuation.

### Rule 5: Premature Closing
- Trigger:
  - `Premature Closing` observed in >= 2 calls.
- Target layer:
  - Strategy Selection.
- Change:
  - Add close-readiness gate requiring:
    - needs confidence >= threshold,
    - no unresolved high-priority objection.
- Expected effect:
  - Fewer pushback/rejection moments.

## Change Control
- Apply at most 1-2 policy deltas per iteration.
- Re-run impacted test IDs plus a small regression subset.
- Keep rollback option if positive outcomes drop by defined threshold.
- Log every change in `iteration_log.md`.

## Validation Requirements Per Iteration
- Compare Iteration N vs N+1 on:
  - positive/neutral/negative distribution,
  - failure-tag frequency,
  - rubric dimension scores.
- Must include short narrative:
  - what changed,
  - why,
  - whether observed effect matched expectation.
