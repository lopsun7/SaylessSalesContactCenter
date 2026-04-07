# Evaluation Rubric

## Purpose
Standardize call quality scoring for consistent diagnosis and iteration comparisons.

## Scoring Scale
- 0 = Failed
- 1 = Weak
- 2 = Adequate
- 3 = Strong
- 4 = Excellent

## Dimensions

### 1) Need Discovery Quality
- Measures whether agent collected key constraints before recommending.
- Indicators:
  - Asked relevant clarifying questions.
  - Captured budget/use-case/priority.

### 2) Recommendation Relevance
- Measures fit between recommendation and user needs.
- Indicators:
  - Aligns with explicit constraints.
  - Includes sensible alternative when needed.

### 3) Objection Handling Quality
- Measures whether objections are directly and effectively addressed.
- Indicators:
  - Response is specific to objection.
  - Uses value/tradeoff framing where appropriate.

### 4) Trust and Risk Handling
- Measures handling of authenticity, warranty, return, reliability concerns.
- Indicators:
  - Acknowledges concern promptly.
  - Provides concrete risk-reduction info.

### 5) Conversation Control and Tone
- Measures pacing, pressure level, and contextual responsiveness.
- Indicators:
  - No pushy premature close.
  - Tone matches user stance and engagement.

### 6) Factual Correctness
- Measures grounding to known catalog/policy facts.
- Indicators:
  - No hallucinated specs, terms, promotions.
  - Claims are verifiable from provided data.

### 7) Closing Appropriateness
- Measures whether call advances to suitable next step.
- Indicators:
  - Close attempt timed correctly.
  - Next-step option is clear and non-coercive.

## Outcome Mapping (Suggested)
- Positive:
  - Average score >= 2.8 and no dimension <= 1.
- Neutral:
  - Average score 2.0-2.79 or one weak dimension without major failure.
- Negative:
  - Average score < 2.0 or major failure tag present (hallucination, trust miss, severe mismatch).

## Required Evaluation Output
- `test_id`
- `iteration`
- `dimension_scores`
- `outcome_label`
- `failure_tags`
- `root_cause_layer`
- `evaluator_notes`

