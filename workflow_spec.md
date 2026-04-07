# Workflow Spec: Response Generation and Improvement Loop

## Design Principle
Improvements must target reusable workflow layers, not one monolithic prompt rewrite.

## Layered Workflow

### 1) Conversation State Tracking
- Input:
  - Transcript turns, previous structured state, current turn metadata.
- Output:
  - Updated structured state:
    - user intent,
    - budget range,
    - product priorities,
    - objections encountered,
    - trust/risk flags,
    - stage (discovery, recommendation, objection handling, closing).
- Responsibility:
  - Keep canonical memory and stage progression.
- Post-call improvement examples:
  - Better recency handling when user changes requirements.
  - Improved slot schema for missing fields.

### 2) User Signal Extraction
- Input:
  - Latest user utterance + current state.
- Output:
  - Signal object:
    - intents,
    - constraints,
    - sentiment,
    - objection labels,
    - ambiguity/conflict tags,
    - confidence values.
- Responsibility:
  - Convert raw text to actionable signals.
- Post-call improvement examples:
  - New detector for trust concerns.
  - Better ambiguity threshold tuning.

### 3) Response Strategy Selection
- Input:
  - Structured state + signals + policy rules.
- Output:
  - Strategy object:
    - turn goal,
    - tactic,
    - close aggressiveness level,
    - required inclusions/exclusions.
- Responsibility:
  - Decide what to do this turn before wording.
- Post-call improvement examples:
  - If price objection repeats, choose value+alternative strategy earlier.
  - Prevent premature closing before readiness criteria.

### 4) Content Planning
- Input:
  - Strategy object + product catalog + compliance/guardrail rules.
- Output:
  - Ordered plan:
    - clarifying question(s),
    - recommendation(s),
    - supporting evidence,
    - risk reduction info,
    - CTA/next-step phrasing.
- Responsibility:
  - Decide message structure and factual content.
- Post-call improvement examples:
  - Reduce info density for low-engagement personas.
  - Insert trust content blocks when trust flag is active.

### 5) Final Response Generation
- Input:
  - Content plan + tone/length policies.
- Output:
  - User-facing response text (voice-ready format).
- Responsibility:
  - Generate concise, coherent, policy-compliant response.
- Post-call improvement examples:
  - Tone adaptation templates.
  - Verbosity control by user engagement signal.

### 6) Post-Call Evaluation
- Input:
  - Full transcript + outcome definition + rubric.
- Output:
  - Outcome label (positive/neutral/negative),
  - failure tags,
  - per-dimension quality scores,
  - root-cause layer attribution.
- Responsibility:
  - Diagnose success/failure with traceable logic.
- Post-call improvement examples:
  - Better attribution precision from failure -> layer.
  - Add evaluator checks for hallucination and pressure tone.

### 7) Script/Policy Optimization
- Input:
  - Batch evaluation outputs + current policy version.
- Output:
  - Versioned policy delta,
  - rationale,
  - expected impact,
  - applied date and affected tests.
- Responsibility:
  - Apply bounded updates based on repeated patterns.
- Post-call improvement examples:
  - Rule addition: "If trust objection appears, answer trust first."
  - Rule adjustment: "If ambiguity confidence < threshold, ask clarifier."

## Data Contracts (Suggested)
- `state.json`
- `signals.json`
- `strategy.json`
- `content_plan.json`
- `response.txt`
- `evaluation.json`
- `policy_version.yaml`

## Guardrails
- No hallucinated product claims outside catalog.
- No close attempt if critical discovery slots are missing.
- If user disengagement rises, shorten and simplify response.

