# High-Value Test Matrix (20 Cases)

## A. Core Happy-Path Cases

| ID | Category | User Profile | User Utterance / Scenario | Expected Agent Behavior | Failure Signals | Possible Improvement Targets |
|---|---|---|---|---|---|---|
| HP-01 | Happy Path | Commuter, mid-budget | "I need earbuds for daily train rides, good noise canceling." | Ask budget/device/fit, recommend balanced ANC option, soft close. | Generic non-targeted pitch. | Need-discovery checklist, stage gating. |
| HP-02 | Happy Path | iPhone user, call-heavy | "Mainly for calls, not music." | Prioritize mic/call quality and iOS compatibility. | Focuses on bass/music only. | Feature-priority mapping. |
| HP-03 | Happy Path | Fitness user | "I need something for running and gym." | Ask about fit/sweat resistance, recommend sport-fit model. | Omits durability/fit concerns. | Use-case question set. |
| HP-04 | Happy Path | New buyer | "I don't know the differences. Help me choose." | Compare 2 options simply, ask preference, guide to decision. | Technical overload. | Response brevity policy. |
| HP-05 | Happy Path | Return customer | "Last pair had poor battery life." | Acknowledge issue, emphasize reliability/warranty, tailored recommendation. | No acknowledgment of prior pain. | Trust recovery template. |

## B. Common Objection Cases

| ID | Category | User Profile | User Utterance / Scenario | Expected Agent Behavior | Failure Signals | Possible Improvement Targets |
|---|---|---|---|---|---|---|
| OBJ-01 | Objection | Price-sensitive student | "Too expensive." | Offer lower tier + value framing + explicit tradeoff. | Repeats same premium model. | Price objection playbook. |
| OBJ-02 | Objection | Skeptical buyer | "Are these authentic?" | Address authenticity/warranty/returns before further pitch. | Ignores trust concern. | Trust-first policy triggers. |
| OBJ-03 | Objection | Busy professional | "I only have 30 seconds." | Switch to concise summary and binary choices. | Continues long script. | Adaptive verbosity logic. |
| OBJ-04 | Objection | Competitor-brand loyal | "I only buy Brand X." | Respect preference, offer neutral comparison, no aggressive push. | Dismissive tone or argument. | Competitive comparison guidelines. |
| OBJ-05 | Objection | Risk-averse shopper | "What if I don't like them?" | Explain return policy + fit tips + low-risk option. | No risk mitigation info. | Risk-reduction response module. |

## C. Difficult / Edge Cases

| ID | Category | User Profile | User Utterance / Scenario | Expected Agent Behavior | Failure Signals | Possible Improvement Targets |
|---|---|---|---|---|---|---|
| EDGE-01 | Edge | Conflicted buyer | "I want best sound and the cheapest price." | Present tradeoff matrix; ask priority ranking. | Chooses one silently. | Conflict-resolution strategy. |
| EDGE-02 | Edge | Ambiguous request | "Need good earbuds." | Ask 2-3 clarifying questions before recommendation. | Immediate recommendation. | Clarification trigger thresholds. |
| EDGE-03 | Edge | Requirement switcher | Changes from premium to budget mid-call. | Update state and re-plan recommendation accordingly. | Continues stale premium path. | State overwrite/recency rules. |
| EDGE-04 | Edge | Unrealistic expectation | "Need 48h battery with ANC at budget price." | Correct politely, provide closest alternatives. | Hallucinates impossible spec. | Catalog-grounded validation gate. |
| EDGE-05 | Edge | Frustrated customer | "Support was terrible last time." | Brief empathy + trust repair + proceed carefully. | Ignores frustration; pushes close. | Tone adaptation rules. |
| EDGE-06 | Edge | Low-engagement user | One-word replies throughout. | Use short guided options and progressive narrowing. | Long paragraphs; user disengages. | Engagement-aware response format. |

## D. Self-Improvement Validation Cases

| ID | Category | User Profile | User Utterance / Scenario | Expected Agent Behavior | Failure Signals | Possible Improvement Targets |
|---|---|---|---|---|---|---|
| IMP-01 | Improvement | Repeated price-objection persona | Repeat price objection scenario across iterations. | Later iteration introduces tiered pricing earlier. | No measurable behavior change. | Price objection policy weight/order. |
| IMP-02 | Improvement | Trust-sensitive persona | Repeat authenticity concern scenario. | Later iteration addresses trust in first relevant response. | Trust still answered late. | Trust signal priority. |
| IMP-03 | Improvement | Ambiguous-intent persona | Repeat vague-input scenario. | Later iteration asks clarifiers before recommending. | Still jumps to recommendation. | Clarification gating rule. |
| IMP-04 | Improvement | Mixed-signals persona | Repeat conflicting priorities scenario. | Later iteration frames explicit tradeoffs. | Repeats one-sided suggestion. | Conflict-handling template. |

## Execution Notes
- Keep persona prompt wording stable per test ID for reproducibility.
- For improvement tests, compare Iteration 0 vs 1 vs 2 with identical input scenarios.
- Log both qualitative failure tags and quantitative rubric scores.

