#!/usr/bin/env python3
"""Text-first baseline pipeline (v0) for the self-improving sales agent.

Pipeline:
state_tracker -> signal_extractor -> strategy_selector -> content_planner
-> response_generator -> evaluator
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Tuple

DEFAULT_POLICY: Dict[str, Any] = {
    "version": "v0",
    "updated_at": "2026-04-07",
    "scope": "wireless_earbuds_text_first",
    "rules": {
        "discovery_required_slots": ["use_case", "budget_tier", "priority"],
        "close_readiness": {
            "min_discovery_confidence": 0.7,
            "allow_if_unresolved_objection": False,
        },
        "objection_handlers": {
            "price": "value_plus_lower_tier",
            "authenticity": "trust_first",
            "time": "compress_and_offer_next_step",
            "returns": "trust_first",
            "brand_loyalty": "recommend_with_tradeoff",
        },
        "objection_priority": [
            "authenticity",
            "returns",
            "price",
            "time",
            "brand_loyalty",
        ],
        "ranking_weights": {
            "budget_match": 4,
            "lower_tier_bonus": 1,
            "over_budget_penalty": 2,
            "priority_match": 3,
            "use_case_match": 2,
            "device_match": 1,
        },
        "response_limits": {
            "max_bullets": 3,
            "max_words_soft": 80,
        },
        "trust_handling": {
            "always_include_if_unresolved": False,
        },
    },
}

DEFAULT_SCRIPT_PACK: Dict[str, Any] = {
    "version": "s0",
    "updated_at": "2026-04-07",
    "templates": {
        "clarify_intro": "To recommend the right earbuds, {questions}",
        "tradeoff_prefix": "There is a tradeoff between price and top-tier sound.",
        "recommend_main": "Based on your needs, I recommend {name} (${price}).",
        "price_alternative": "For a lower-cost alternative, {name} (${price}) keeps strong value.",
        "price_fallback": "I will keep this value-focused and avoid higher-priced options.",
        "cta_soft": "If you want, I can narrow this down to one final pick.",
        "cta_close": "If this sounds right, I can help you finalize one option now.",
    },
    "snippets": {
        "trust_authentic": "All products are authentic with {warranty_months}-month warranty.",
        "trust_return": "You also get a {return_policy_days}-day return window.",
    },
    "style": {
        "max_words_soft": 85,
        "prefer_short_sentences": True,
    },
}


def load_json_or_yaml(path: Path) -> Dict[str, Any]:
    """Load JSON, or JSON-compatible YAML (without external dependencies)."""
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path} is not JSON-compatible. Use JSON syntax inside .yaml files "
            "or install a YAML parser."
        ) from exc


def load_policy(path: Path | None) -> Dict[str, Any]:
    if path is None:
        return deepcopy(DEFAULT_POLICY)
    loaded = load_json_or_yaml(path)
    return normalize_policy(loaded)


def load_script_pack(path: Path | None) -> Dict[str, Any]:
    if path is None:
        return deepcopy(DEFAULT_SCRIPT_PACK)
    loaded = load_json_or_yaml(path)
    return normalize_script_pack(loaded)


def normalize_policy(policy: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(DEFAULT_POLICY)
    if not isinstance(policy, dict):
        return merged

    for k, v in policy.items():
        if k != "rules":
            merged[k] = v

    rules = policy.get("rules", {})
    if isinstance(rules, dict):
        for key, value in rules.items():
            if key in merged["rules"] and isinstance(merged["rules"][key], dict) and isinstance(value, dict):
                merged["rules"][key].update(value)
            else:
                merged["rules"][key] = value

    return merged


def normalize_script_pack(script_pack: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(DEFAULT_SCRIPT_PACK)
    if not isinstance(script_pack, dict):
        return merged

    for key in ("version", "updated_at"):
        if key in script_pack:
            merged[key] = script_pack[key]

    for key in ("templates", "snippets", "style"):
        value = script_pack.get(key)
        if isinstance(value, dict):
            merged[key].update(value)

    return merged


def _resolve_tactic(handler_name: str) -> str:
    mapping = {
        "compress_response": "compress_and_offer_next_step",
        "risk_reduction": "trust_first",
    }
    return mapping.get(handler_name, handler_name)


def initial_state(conversation_id: str) -> Dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "turn_index": 0,
        "stage": "discovery",
        "slots": {
            "use_case": "unknown",
            "budget_tier": "unknown",
            "priority": "unknown",
            "device": "unknown",
            "trust_sensitive": False,
            "conflict_flags": [],
        },
        "memory": {
            "use_case": "",
            "budget": "",
            "priority": "",
            "device": "",
            "intent": "",
            "objections": [],
            "trust_concerns": [],
            "notes": "",
        },
        "unresolved_objections": [],
        "history": [],
    }


def _contains_any(text: str, words: List[str]) -> bool:
    return any(w in text for w in words)


USE_CASE_EQUIVALENTS = {
    # Catalog is earbuds-only, so gaming maps to sound/calls-adjacent profiles.
    "gaming": {"music", "calls", "casual", "work"},
}


def detect_budget_tier(text: str) -> str:
    nums = [int(n) for n in re.findall(r"\b(\d{2,4})\b", text)]
    if nums:
        value = min(nums)
        if value <= 100:
            return "budget"
        if value <= 170:
            return "mid"
        return "premium"

    if _contains_any(text, ["cheap", "budget", "affordable", "student", "lowest"]):
        return "budget"
    if _contains_any(text, ["premium", "best", "flagship"]):
        return "premium"

    if _contains_any(text, ["mid", "middle range"]):
        return "mid"
    return "unknown"


def detect_priority(text: str) -> str:
    if _contains_any(text, ["noise", "anc", "quiet"]):
        return "anc"
    if _contains_any(text, ["call", "mic", "meeting", "zoom"]):
        return "calls"
    if _contains_any(text, ["gym", "run", "workout", "fit"]):
        return "fit"
    if _contains_any(text, ["battery"]):
        return "battery"
    if _contains_any(text, ["cheap", "value", "price", "affordable"]):
        return "value"
    if _contains_any(text, ["gaming", "game", "fps", "latency", "footstep"]):
        return "sound"
    if _contains_any(text, ["sound", "music", "bass"]):
        return "sound"
    if _contains_any(text, ["authentic", "genuine", "warranty", "reliable"]):
        return "reliability"
    return "unknown"


def detect_use_case(text: str) -> str:
    if _contains_any(text, ["train", "commute", "bus", "travel"]):
        return "commute"
    if _contains_any(text, ["gaming", "game", "fps", "esports"]):
        return "gaming"
    if _contains_any(text, ["call", "meeting", "work"]):
        return "calls"
    if _contains_any(text, ["gym", "running", "run", "workout"]):
        return "gym"
    if _contains_any(text, ["music", "song", "audio"]):
        return "music"
    return "unknown"


def detect_device(text: str) -> str:
    if _contains_any(text, ["iphone", "ios"]):
        return "ios"
    if _contains_any(text, ["android", "samsung", "pixel"]):
        return "android"
    return "unknown"


def detect_intent(text: str) -> str:
    if _contains_any(text, ["buy", "purchase", "take it", "order now"]):
        return "buy"
    if _contains_any(text, ["later", "not now", "think about it"]):
        return "defer"
    if _contains_any(text, ["not interested", "no thanks"]):
        return "reject"
    if _contains_any(text, ["compare", "difference", "which one"]):
        return "compare"
    if _contains_any(text, ["recommend", "need", "want", "looking"]):
        return "explore"
    return "unknown"


def extract_signals(user_text: str, state: Dict[str, Any]) -> Dict[str, Any]:
    text = user_text.strip().lower()

    objections: List[str] = []
    trust_flags: List[str] = []

    if _contains_any(text, ["too expensive", "expensive", "costly", "over budget"]):
        objections.append("price")
    if _contains_any(text, ["authentic", "genuine", "fake"]):
        objections.append("authenticity")
        trust_flags.append("authenticity")
    if _contains_any(text, ["30 seconds", "no time", "busy"]):
        objections.append("time")
    if _contains_any(text, ["return", "refund", "don't like", "dont like"]):
        objections.append("returns")
        trust_flags.append("returns")
    if _contains_any(text, ["only buy brand", "only buy", "brand x"]):
        objections.append("brand_loyalty")

    conflicts: List[str] = []
    if (_contains_any(text, ["cheapest", "cheap", "low price", "budget"]) and
            _contains_any(text, ["best", "top", "premium", "best sound"])):
        conflicts.append("price_vs_quality")

    use_case = detect_use_case(text)
    budget_tier = detect_budget_tier(text)
    priority = detect_priority(text)
    device = detect_device(text)

    detected_fields = [use_case, budget_tier, priority, device]
    detected_count = sum(1 for x in detected_fields if x != "unknown")

    ambiguity = {
        "is_ambiguous": detected_count == 0 and len(text.split()) <= 5,
        "reason": "insufficient_constraints" if detected_count == 0 else "",
    }

    engagement = "low" if len(text.split()) <= 4 or "busy" in text else "medium"
    if len(text.split()) >= 12:
        engagement = "high"

    confidence = {
        "overall": min(1.0, 0.25 + (detected_count * 0.2)),
        "constraint_confidence": min(1.0, detected_count / 4.0),
    }

    return {
        "intent": detect_intent(text),
        "constraints": {
            "budget_tier": budget_tier,
            "priority": priority,
            "use_case": use_case,
            "device": device,
        },
        "objections": objections,
        "trust_flags": trust_flags,
        "ambiguity": ambiguity,
        "conflicts": conflicts,
        "engagement": engagement,
        "confidence": confidence,
    }


def update_state(
    state: Dict[str, Any],
    signals: Dict[str, Any],
    user_text: str,
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    next_state = deepcopy(state)
    next_state["turn_index"] += 1

    for slot in ["use_case", "budget_tier", "priority", "device"]:
        value = signals["constraints"][slot]
        if value != "unknown":
            next_state["slots"][slot] = value

    if signals["trust_flags"]:
        next_state["slots"]["trust_sensitive"] = True

    for conflict in signals["conflicts"]:
        if conflict not in next_state["slots"]["conflict_flags"]:
            next_state["slots"]["conflict_flags"].append(conflict)

    for obj in signals["objections"]:
        if obj not in next_state["unresolved_objections"]:
            next_state["unresolved_objections"].append(obj)

    text = user_text.lower()
    if "ok" in text or "that works" in text:
        next_state["unresolved_objections"] = []

    required_slot_names = policy["rules"].get(
        "discovery_required_slots",
        ["use_case", "budget_tier", "priority"],
    )
    required_slots = [next_state["slots"].get(slot, "unknown") for slot in required_slot_names]
    missing_required = any(value == "unknown" for value in required_slots)

    if next_state["unresolved_objections"]:
        next_state["stage"] = "objection_handling"
    elif missing_required:
        next_state["stage"] = "discovery"
    elif signals["intent"] == "buy":
        next_state["stage"] = "closing"
    else:
        next_state["stage"] = "recommendation"

    return next_state


def select_strategy(
    state: Dict[str, Any],
    signals: Dict[str, Any],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    required_slot_names = policy["rules"].get(
        "discovery_required_slots",
        ["use_case", "budget_tier", "priority"],
    )
    missing_required = any(
        state["slots"].get(slot, "unknown") == "unknown"
        for slot in required_slot_names
    )

    strategy = {
        "goal": "recommend",
        "tactic": "recommend_with_tradeoff",
        "ask_clarification": False,
        "close_level": "soft",
        "focus_points": ["priority", "budget", "compatibility"],
        "avoid": ["hallucination", "info_overload", "premature_close", "pushy_tone"],
    }

    objections = set(signals["objections"]) | set(state["unresolved_objections"])
    objection_priority = policy["rules"].get(
        "objection_priority",
        ["authenticity", "returns", "price", "time", "brand_loyalty"],
    )
    handlers = policy["rules"].get("objection_handlers", {})

    selected_objection = ""
    for objection in objection_priority:
        if objection in objections:
            selected_objection = objection
            break

    if selected_objection:
        tactic = _resolve_tactic(handlers.get(selected_objection, "recommend_with_tradeoff"))
        strategy.update(
            {
                "goal": "handle_objection",
                "tactic": tactic,
                "close_level": "none",
                "focus_points": ["objection"],
            }
        )
        if selected_objection == "price":
            strategy["focus_points"] = ["budget", "tradeoff", "objection"]
        elif selected_objection in {"authenticity", "returns"}:
            strategy["focus_points"] = ["warranty", "returns", "objection"]
        elif selected_objection == "time":
            strategy["focus_points"] = ["priority", "budget"]
        return strategy

    if signals["conflicts"] or state["slots"]["conflict_flags"]:
        if missing_required:
            strategy.update(
                {
                    "goal": "discover",
                    "tactic": "ask_2_questions",
                    "ask_clarification": True,
                    "close_level": "none",
                    "focus_points": ["tradeoff", "priority", "budget"],
                }
            )
        else:
            strategy.update(
                {
                    "goal": "recommend",
                    "tactic": "recommend_with_tradeoff",
                    "close_level": "soft",
                    "focus_points": ["tradeoff", "priority", "budget"],
                }
            )
        return strategy

    if signals["ambiguity"]["is_ambiguous"] or missing_required:
        strategy.update(
            {
                "goal": "discover",
                "tactic": "ask_2_questions",
                "ask_clarification": True,
                "close_level": "none",
                "focus_points": ["use_case", "budget", "priority"],
            }
        )
        return strategy

    if state["stage"] == "closing":
        min_conf = policy["rules"].get("close_readiness", {}).get("min_discovery_confidence", 0.7)
        allow_unresolved = policy["rules"].get("close_readiness", {}).get("allow_if_unresolved_objection", False)
        can_close = signals["confidence"].get("constraint_confidence", 0.0) >= min_conf
        if not allow_unresolved and state["unresolved_objections"]:
            can_close = False
        if can_close:
            strategy.update({"goal": "close", "tactic": "soft_close", "close_level": "soft"})
        else:
            strategy.update(
                {
                    "goal": "discover",
                    "tactic": "ask_2_questions",
                    "ask_clarification": True,
                    "close_level": "none",
                }
            )

    return strategy


def _tier_rank(tier: str) -> int:
    return {"budget": 0, "mid": 1, "premium": 2}.get(tier, 1)


def _matches_use_case(user_use_case: str, product_use_cases: List[str]) -> bool:
    if user_use_case in product_use_cases:
        return True
    equivalents = USE_CASE_EQUIVALENTS.get(user_use_case, set())
    return bool(equivalents.intersection(set(product_use_cases)))


def recommend_products(
    catalog: Dict[str, Any],
    state: Dict[str, Any],
    signals: Dict[str, Any],
    strategy: Dict[str, Any],
    policy: Dict[str, Any],
) -> Tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
    products = catalog["products"]
    budget_tier = state["slots"]["budget_tier"]
    priority = state["slots"]["priority"]
    use_case = state["slots"]["use_case"]
    device = state["slots"]["device"]

    weights = policy["rules"].get("ranking_weights", {})
    budget_match_weight = int(weights.get("budget_match", 4))
    lower_tier_bonus = int(weights.get("lower_tier_bonus", 1))
    over_budget_penalty = int(weights.get("over_budget_penalty", 2))
    priority_match_weight = int(weights.get("priority_match", 3))
    use_case_match_weight = int(weights.get("use_case_match", 2))
    device_match_weight = int(weights.get("device_match", 1))

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for product in products:
        score = 0
        if budget_tier != "unknown":
            if product["tier"] == budget_tier:
                score += budget_match_weight
            elif _tier_rank(product["tier"]) < _tier_rank(budget_tier):
                score += lower_tier_bonus
            else:
                # Penalize suggestions above declared budget tier.
                score -= over_budget_penalty * (_tier_rank(product["tier"]) - _tier_rank(budget_tier))
        if priority != "unknown":
            if priority in product["strengths"]:
                score += priority_match_weight
            if priority == "calls" and product["features"]["mic_quality"] in ("very_good", "good"):
                score += 1
            if priority == "anc" and product["features"]["anc"] in ("medium", "strong"):
                score += 1
        if use_case != "unknown" and _matches_use_case(use_case, product["use_cases"]):
            score += use_case_match_weight
        if device != "unknown" and device in product["compatibility"]:
            score += device_match_weight

        scored.append((score, product))

    scored.sort(key=lambda item: (item[0], -item[1]["price_usd"]), reverse=True)
    primary = scored[0][1] if scored else None

    alternative = None
    if primary:
        lower_options = [p for _, p in scored if p["price_usd"] < primary["price_usd"]]
        if lower_options:
            alternative = sorted(lower_options, key=lambda p: p["price_usd"])[0]

    if strategy["tactic"] == "value_plus_lower_tier":
        budget_sorted = sorted(products, key=lambda p: p["price_usd"])
        alternative = budget_sorted[0]

    return primary, alternative


def plan_content(
    strategy: Dict[str, Any],
    state: Dict[str, Any],
    signals: Dict[str, Any],
    catalog: Dict[str, Any],
    policy: Dict[str, Any],
    script_pack: Dict[str, Any],
) -> Dict[str, Any]:
    plan: Dict[str, Any] = {
        "questions": [],
        "recommendations": [],
        "supporting_points": [],
        "trust_block": [],
        "cta": "",
    }

    if strategy["ask_clarification"]:
        if state["slots"]["budget_tier"] == "unknown":
            plan["questions"].append("What budget range are you targeting?")
        if state["slots"]["use_case"] == "unknown":
            plan["questions"].append("What is your main use (calls, commute, gym, music, gaming, or other)?")
        if state["slots"]["priority"] == "unknown":
            plan["questions"].append("What matters most: ANC, call quality, fit, sound/latency, or price?")
        return plan

    primary, alternative = recommend_products(catalog, state, signals, strategy, policy)
    if primary:
        plan["recommendations"].append(
            {
                "id": primary["id"],
                "name": primary["name"],
                "price_usd": primary["price_usd"],
            }
        )

    if alternative and alternative["id"] != primary["id"]:
        plan["recommendations"].append(
            {
                "id": alternative["id"],
                "name": alternative["name"],
                "price_usd": alternative["price_usd"],
            }
        )

    priority = state["slots"]["priority"]
    if priority == "anc":
        plan["supporting_points"].append("Focused on commute comfort and noise control.")
    elif priority == "calls":
        plan["supporting_points"].append("Optimized for clear calls and microphone quality.")
    elif priority == "fit":
        plan["supporting_points"].append("Designed for secure fit during movement.")
    elif priority == "sound":
        plan["supporting_points"].append("Tuned for immersive sound and clear directional detail.")
    elif priority == "value":
        plan["supporting_points"].append("Best value option while keeping core quality.")

    if signals["conflicts"] or state["slots"]["conflict_flags"]:
        plan["supporting_points"].append(
            "There is a tradeoff between best sound and lowest price; we can prioritize one first."
        )

    unresolved_trust = any(obj in state["unresolved_objections"] for obj in ("authenticity", "returns"))
    trust_required = (
        bool(signals["trust_flags"])
        or "authenticity" in signals["objections"]
        or "returns" in signals["objections"]
    )
    if policy["rules"].get("trust_handling", {}).get("always_include_if_unresolved", False):
        trust_required = trust_required or unresolved_trust

    if trust_required:
        snippets = script_pack.get("snippets", {})
        authentic_line = snippets.get(
            "trust_authentic",
            DEFAULT_SCRIPT_PACK["snippets"]["trust_authentic"],
        )
        return_line = snippets.get(
            "trust_return",
            DEFAULT_SCRIPT_PACK["snippets"]["trust_return"],
        )
        plan["trust_block"] = [
            authentic_line.format(warranty_months=catalog["warranty_months"]),
            return_line.format(return_policy_days=catalog["return_policy_days"]),
        ]

    templates = script_pack.get("templates", {})
    if strategy["goal"] == "close":
        plan["cta"] = templates.get("cta_close", DEFAULT_SCRIPT_PACK["templates"]["cta_close"])
    else:
        plan["cta"] = templates.get("cta_soft", DEFAULT_SCRIPT_PACK["templates"]["cta_soft"])

    return plan


def generate_response(
    plan: Dict[str, Any],
    strategy: Dict[str, Any],
    state: Dict[str, Any],
    policy: Dict[str, Any],
    script_pack: Dict[str, Any],
) -> str:
    templates = script_pack.get("templates", {})
    if strategy["ask_clarification"]:
        questions = plan["questions"][:2]
        if not questions:
            questions = ["What budget and main use should I optimize for?"]
        prefix = ""
        if "tradeoff" in strategy["focus_points"]:
            prefix = templates.get(
                "tradeoff_prefix",
                DEFAULT_SCRIPT_PACK["templates"]["tradeoff_prefix"],
            ) + " "
        clarify_intro = templates.get(
            "clarify_intro",
            DEFAULT_SCRIPT_PACK["templates"]["clarify_intro"],
        )
        return prefix + clarify_intro.format(questions=" ".join(questions))

    lines: List[str] = []
    recs = plan["recommendations"]

    if recs:
        main = recs[0]
        lines.append(
            templates.get("recommend_main", DEFAULT_SCRIPT_PACK["templates"]["recommend_main"]).format(
                name=main["name"],
                price=main["price_usd"],
            )
        )

    if strategy["tactic"] == "value_plus_lower_tier" and len(recs) > 1:
        alt = recs[1]
        lines.append(
            templates.get(
                "price_alternative",
                DEFAULT_SCRIPT_PACK["templates"]["price_alternative"],
            ).format(name=alt["name"], price=alt["price_usd"])
        )
    elif strategy["tactic"] == "value_plus_lower_tier":
        lines.append(templates.get("price_fallback", DEFAULT_SCRIPT_PACK["templates"]["price_fallback"]))

    if plan["supporting_points"]:
        lines.append(plan["supporting_points"][0])
        if "tradeoff" in plan["supporting_points"][-1].lower() and len(plan["supporting_points"]) > 1:
            lines.append(plan["supporting_points"][-1])

    if plan["trust_block"]:
        lines.extend(plan["trust_block"])

    if plan["cta"] and strategy["close_level"] != "none":
        lines.append(plan["cta"])

    response = " ".join(lines)
    words = response.split()
    max_words = int(
        min(
            policy["rules"].get("response_limits", {}).get("max_words_soft", 80),
            script_pack.get("style", {}).get("max_words_soft", 85),
        )
    )
    if len(words) > max_words:
        response = " ".join(words[:max_words])
    return response


def evaluate(
    test_id: str,
    iteration: str,
    state: Dict[str, Any],
    last_signals: Dict[str, Any],
    last_strategy: Dict[str, Any],
    last_plan: Dict[str, Any],
    last_response: str,
    last_user_turn: str,
) -> Dict[str, Any]:
    slots = state["slots"]
    required = [slots["use_case"], slots["budget_tier"], slots["priority"]]
    filled = sum(1 for s in required if s != "unknown")

    need_discovery = max(0, min(4, round((filled / 3) * 4)))
    if last_strategy["goal"] == "discover":
        need_discovery = max(need_discovery, 1)

    has_recommendation = bool(last_plan["recommendations"])
    recommendation_relevance = 3 if has_recommendation else 1
    if slots["priority"] == "unknown":
        recommendation_relevance = min(recommendation_relevance, 2)

    response_l = last_response.lower()
    objections = set(state["unresolved_objections"]) | set(last_signals["objections"])
    if "price" in objections:
        objection_handling = 3 if _contains_any(response_l, ["lower", "alternative", "value"]) else 1
    elif objections:
        objection_handling = 2 if _contains_any(response_l, ["warranty", "return", "authentic", "option"]) else 1
    else:
        objection_handling = 3

    trust_needed = bool(last_signals["trust_flags"] or "authenticity" in objections or "returns" in objections)
    trust_risk = 4
    if trust_needed and not _contains_any(response_l, ["warranty", "return", "authentic"]):
        trust_risk = 1

    wc = len(last_response.split())
    tone_control = 4
    if wc > 90:
        tone_control = 1
    elif wc > 70:
        tone_control = 2
    elif wc > 55:
        tone_control = 3

    factual_correctness = 4
    if re.search(r"\b(\d{2,3})h\b", response_l):
        factual_correctness = 3

    close_phrase = _contains_any(response_l, ["finalize", "final pick", "buy now"])
    closing_appropriateness = 3
    if close_phrase and filled < 3:
        closing_appropriateness = 1
    elif close_phrase and filled == 3:
        closing_appropriateness = 4

    failure_tags: List[str] = []
    if last_strategy["goal"] in {"recommend", "close"} and filled < 3:
        failure_tags.append("missed_need_discovery")
    if close_phrase and filled < 3:
        failure_tags.append("premature_closing")
    if trust_needed and trust_risk <= 1:
        failure_tags.append("trust_not_addressed")
    if "price" in objections and objection_handling <= 1:
        failure_tags.append("wrong_objection_handling")
    if (last_signals["conflicts"] or slots["conflict_flags"]) and not _contains_any(response_l, ["tradeoff", "between", "prioritize"]):
        failure_tags.append("conflicting_signals_mishandled")
    if wc > 90:
        failure_tags.append("information_overload")

    if has_recommendation and slots["budget_tier"] == "budget":
        price = last_plan["recommendations"][0]["price_usd"]
        if price > 170:
            failure_tags.append("recommendation_mismatch")

    score_map = {
        "need_discovery": need_discovery,
        "recommendation_relevance": recommendation_relevance,
        "objection_handling": objection_handling,
        "trust_risk": trust_risk,
        "tone_control": tone_control,
        "factual_correctness": factual_correctness,
        "closing_appropriateness": closing_appropriateness,
    }

    avg_score = mean(score_map.values())
    major_failures = {"hallucinated_claims", "trust_not_addressed", "premature_closing", "recommendation_mismatch"}

    user_l = last_user_turn.lower()
    if _contains_any(user_l, ["buy", "take it", "order now"]):
        outcome = "positive"
    elif major_failures.intersection(failure_tags):
        outcome = "negative"
    elif avg_score >= 3.0:
        outcome = "positive"
    else:
        outcome = "neutral"

    root_map = {
        "missed_need_discovery": "state_tracking",
        "recommendation_mismatch": "strategy_selection",
        "wrong_objection_handling": "strategy_selection",
        "information_overload": "response_generation",
        "premature_closing": "strategy_selection",
        "trust_not_addressed": "content_planning",
        "conflicting_signals_mishandled": "state_tracking",
        "tone_pressure_mismatch": "response_generation",
        "hallucinated_claims": "content_planning",
    }
    kind_map = {
        "missed_need_discovery": "decision",
        "recommendation_mismatch": "decision",
        "wrong_objection_handling": "decision",
        "information_overload": "expression",
        "premature_closing": "decision",
        "trust_not_addressed": "decision",
        "conflicting_signals_mishandled": "decision",
        "tone_pressure_mismatch": "expression",
        "hallucinated_claims": "decision",
    }
    root_cause = root_map[failure_tags[0]] if failure_tags else "evaluation"
    root_kind = kind_map[failure_tags[0]] if failure_tags else "none"

    return {
        "test_id": test_id,
        "iteration": iteration,
        "dimension_scores": score_map,
        "outcome_label": outcome,
        "failure_tags": failure_tags,
        "root_cause_layer": root_cause,
        "root_cause_kind": root_kind,
        "notes": f"avg_score={avg_score:.2f}",
    }


def validate_expectations(
    expected: Dict[str, Any],
    evaluation: Dict[str, Any],
    final_response: str,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    passed = True

    allowed = expected.get("allowed_outcomes", [])
    if allowed and evaluation["outcome_label"] not in allowed:
        passed = False
        reasons.append(
            f"outcome={evaluation['outcome_label']} not in allowed_outcomes={allowed}"
        )

    forbidden = set(expected.get("forbidden_failure_tags", []))
    actual_tags = set(evaluation["failure_tags"])
    bad_tags = sorted(forbidden.intersection(actual_tags))
    if bad_tags:
        passed = False
        reasons.append(f"forbidden failure tags present: {bad_tags}")

    required_tags = set(expected.get("required_failure_tags", []))
    missing_required_tags = sorted(required_tags.difference(actual_tags))
    if missing_required_tags:
        passed = False
        reasons.append(f"missing required failure tags: {missing_required_tags}")

    keywords_any = expected.get("required_response_keywords_any", [])
    if keywords_any:
        response_l = final_response.lower()
        if not any(k.lower() in response_l for k in keywords_any):
            passed = False
            reasons.append(
                "response does not contain any required keywords: "
                f"{keywords_any}"
            )

    min_scores = expected.get("min_scores", {})
    for dim, threshold in min_scores.items():
        actual = evaluation["dimension_scores"].get(dim)
        if actual is None or actual < threshold:
            passed = False
            reasons.append(f"score[{dim}]={actual} < min={threshold}")

    return passed, reasons


def run_case(
    case: Dict[str, Any],
    personas: Dict[str, Any],
    catalog: Dict[str, Any],
    iteration: str,
    policy: Dict[str, Any],
    script_pack: Dict[str, Any],
) -> Dict[str, Any]:
    case_id = case["id"]
    state = initial_state(case_id)

    last_signals: Dict[str, Any] = {}
    last_strategy: Dict[str, Any] = {}
    last_plan: Dict[str, Any] = {}
    last_response = ""
    last_user = ""

    for user_turn in case["user_turns"]:
        last_user = user_turn
        state["history"].append({"role": "user", "text": user_turn})

        signals = extract_signals(user_turn, state)
        state = update_state(state, signals, user_turn, policy)
        strategy = select_strategy(state, signals, policy)
        plan = plan_content(strategy, state, signals, catalog, policy, script_pack)
        response = generate_response(plan, strategy, state, policy, script_pack)

        state["history"].append({"role": "agent", "text": response})

        last_signals = signals
        last_strategy = strategy
        last_plan = plan
        last_response = response

    evaluation = evaluate(
        test_id=case_id,
        iteration=iteration,
        state=state,
        last_signals=last_signals,
        last_strategy=last_strategy,
        last_plan=last_plan,
        last_response=last_response,
        last_user_turn=last_user,
    )

    passed, reasons = validate_expectations(case.get("expected", {}), evaluation, last_response)

    return {
        "id": case_id,
        "category": case["category"],
        "persona_id": case["persona_id"],
        "pass": passed,
        "reasons": reasons,
        "evaluation": evaluation,
        "final_response": last_response,
        "final_state": state,
        "transcript": state["history"],
        "track_for_iterations": bool(case.get("track_for_iterations", False)),
    }


def run_suite(
    cases_file: Path,
    personas_file: Path,
    catalog_file: Path,
    policy_file: Path | None = None,
    script_file: Path | None = None,
    iteration: str | None = None,
) -> Dict[str, Any]:
    suite = load_json_or_yaml(cases_file)
    personas = load_json_or_yaml(personas_file)
    catalog = load_json_or_yaml(catalog_file)
    policy = load_policy(policy_file)
    script_pack = load_script_pack(script_file)

    resolved_iteration = iteration or policy.get("version") or suite.get("iteration", "v0")
    results = [
        run_case(case, personas, catalog, resolved_iteration, policy, script_pack)
        for case in suite["cases"]
    ]

    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed

    return {
        "suite": suite.get("suite", "unnamed_suite"),
        "iteration": resolved_iteration,
        "policy_version": policy.get("version", "unknown"),
        "script_version": script_pack.get("version", "unknown"),
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
        },
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run baseline v0 executable test suite")
    parser.add_argument(
        "--cases",
        default="tests/executable_cases.yaml",
        type=Path,
        help="Path to test case suite",
    )
    parser.add_argument(
        "--personas",
        default="data/test_personas.yaml",
        type=Path,
        help="Path to personas file",
    )
    parser.add_argument(
        "--catalog",
        default="data/product_catalog.json",
        type=Path,
        help="Path to product catalog",
    )
    parser.add_argument(
        "--out",
        default="tests/latest_run_v0.json",
        type=Path,
        help="Path to write run output",
    )
    parser.add_argument(
        "--policy",
        default="config/policy_version.yaml",
        type=Path,
        help="Path to policy version file",
    )
    parser.add_argument(
        "--scripts",
        default="assets/script_pack_v0.json",
        type=Path,
        help="Path to script/prompt assets file",
    )
    parser.add_argument(
        "--iteration",
        default=None,
        type=str,
        help="Optional iteration label override",
    )
    parser.add_argument(
        "--show-failures-only",
        action="store_true",
        help="Print only failed case summaries",
    )
    args = parser.parse_args()

    report = run_suite(
        args.cases,
        args.personas,
        args.catalog,
        args.policy,
        args.scripts,
        args.iteration,
    )
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(
        f"Suite: {report['suite']} ({report['iteration']}, "
        f"policy={report['policy_version']}, scripts={report['script_version']})"
    )
    print(
        "Summary: "
        f"{report['summary']['passed']}/{report['summary']['total']} passed, "
        f"{report['summary']['failed']} failed"
    )

    for result in report["results"]:
        if args.show_failures_only and result["pass"]:
            continue
        status = "PASS" if result["pass"] else "FAIL"
        print(
            f"- [{status}] {result['id']} | outcome={result['evaluation']['outcome_label']} "
            f"| failures={result['evaluation']['failure_tags']}"
        )
        if not result["pass"]:
            for reason in result["reasons"]:
                print(f"    reason: {reason}")


if __name__ == "__main__":
    main()
