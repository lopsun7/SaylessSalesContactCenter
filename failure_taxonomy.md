# Failure Taxonomy: Sales-Call Failure Modes

## Purpose
Provide a structured map from observed call failure -> diagnosis -> workflow layer target for improvement.

| Failure Type | What It Means | How It Appears in Conversation | Primary Workflow Layer Mapping |
|---|---|---|---|
| Missed Need Discovery | Agent fails to collect key preferences/constraints before recommending. | Recommends too early without budget/use-case/device/fit questions. | User Signal Extraction, Conversation State Tracking |
| Recommendation Mismatch | Suggested product does not align with explicit user needs. | User asks for budget option; agent pushes premium ANC model. | Strategy Selection, Content Planning |
| Wrong Objection Handling | Agent response does not address the user's stated objection. | User says "too expensive"; agent repeats specs instead of value/tier alternatives. | Strategy Selection, Improvement Policy |
| Information Overload | Agent gives too much technical detail at once. | Long monologue; user confusion, silence, or "too much info". | Content Planning, Final Response Generation |
| Premature Closing | Agent pushes close before readiness signals are met. | Attempts checkout before needs/objections are resolved. | Strategy Selection, Stage Logic |
| Trust Not Addressed | Agent ignores risk/trust concerns (authenticity, warranty, returns). | User asks if product is genuine; agent ignores and keeps selling features. | User Signal Extraction, Content Planning |
| Conflicting Signals Mishandled | Agent fails to reconcile contradictory user priorities. | User wants "best sound at lowest price"; agent picks one side without tradeoff framing. | State Tracking, Strategy Selection |
| Poor Turn Management | Agent does not respond to latest user context. | Continues prior recommendation after user changes requirements. | State Tracking, Response Generation |
| Weak Clarification Behavior | Agent does not clarify ambiguous user requests. | User says "good ones"; agent skips clarifying questions. | Signal Extraction, Strategy Selection |
| Tone/Pressure Mismatch | Agent style is pushy, robotic, or not adapted to user state. | Repeated urgency despite hesitant user. | Final Response Generation, Guardrails |
| Hallucinated Claims | Agent invents product facts, promo terms, or compatibility. | Mentions specs/discounts absent from catalog. | Content Planning, Response Validation |
| No Recoverability | Agent cannot recover after rejection or friction. | Ends abruptly after first "not interested". | Strategy Selection, Fallback Policy |

## Layer Mapping Summary
- State/signal errors: Missed Need Discovery, Conflicting Signals, Poor Turn Management, Weak Clarification.
- Strategy errors: Premature Closing, Wrong Objection Handling, No Recoverability.
- Planning/generation errors: Information Overload, Tone Mismatch, Trust Not Addressed, Hallucinations.

