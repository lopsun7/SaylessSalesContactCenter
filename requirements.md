# Requirements: Self-Improving Call Center Sales Agent

## 1. Business Scenario
Build a scoped simulation of an e-commerce sales assistant (wireless earbuds domain) that:
- runs a sales conversation,
- identifies customer needs,
- recommends products,
- handles objections,
- logs outcomes,
- learns from outcomes through targeted policy updates.

This project is a hiring assessment artifact optimized for clarity, reproducibility, and explainability.

## 2. User Types
- Prospective buyer: end user on the simulated sales call.
- Sales operations reviewer: inspects call quality and improvement logic.
- Hiring assessor: evaluates architecture, tests, and iteration evidence.
- System operator (developer): configures rules, runs tests, applies improvements.

## 3. Agent Goals
- Conduct coherent, context-aware sales dialogue.
- Discover needs before recommending.
- Handle objections with relevant responses.
- Attempt a suitable close or next step.
- Produce diagnosable post-call analysis.
- Improve behavior through modular, traceable updates.

## 4. Non-Goals
- Production telephony/CCaaS integration.
- Full CRM or payment integration.
- Broad multi-category catalog coverage.
- Human-level persuasion across all personalities.
- End-to-end deployment hardening.

## 5. Success Criteria
- Agent can simulate a sales conversation (voice-ready, text-first acceptable).
- Implements feedback loop: outcome -> analysis -> script/policy adjustment.
- Improvement logic is documented as explicit rules.
- Demo includes at least 2 iteration cycles.
- Measurable reduction in repeated failure patterns across iterations.
- All updates are traceable (what changed, why, and result).

## 6. Outcome Definitions
- Positive:
  - User accepts recommendation, commits to purchase intent, or agrees to concrete next step.
- Neutral:
  - No conversion, but conversation remains coherent/trust-preserving with valid deferral.
- Negative:
  - User disengages/frustrates, trust drops, recommendation is clearly mismatched, or flow breaks.

## 7. Assumptions and Constraints
- Text simulation is primary; voice layer is optional/mock.
- Domain is initially wireless earbuds only.
- Calls are short (about 3-12 turns).
- Improvements are modular (by workflow layer), not monolithic prompt rewrites.
- Product facts come from a structured local catalog.
- Synthetic personas/test scenarios are acceptable.
- Scope is deliberately limited for take-home completion.
- Design prioritizes reliability, diagnosability, and traceability over breadth.

