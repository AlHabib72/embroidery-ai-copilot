"""
Structured command interpretation layer.

Deliberately rule/keyword based, NOT an LLM call — see PROJECT_CONTEXT
section 17: "Do NOT execute raw natural language directly against Wilcom."
The pipeline is: natural language -> intent parser -> structured command ->
validation against what was actually detected -> risk assessment -> (the
caller decides whether to seek approval and execute).

CRITICAL ANTI-HALLUCINATION RULE (section 42): if a command refers to a
category or object that was not actually detected, this module says so
plainly instead of inventing a match. "Punch all leaves" against a
blueprint with zero leaf-category objects returns a command with
matched_object_ids=[] and a "not_found" note, never a fabricated success.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .domain import CommandKind
from .vision import ObjectBlueprint

# ----------------------------------------------------------------------


@dataclass
class StructuredCommand:
    raw_text: str
    intent: str
    kind: str  # CommandKind value
    target_category: str | None = None
    scope: str = "entire_design"  # "entire_design" | "category" | "all"
    constraints: dict = field(default_factory=dict)
    matched_object_ids: list[str] = field(default_factory=list)
    note: str = ""


# Verb -> (intent, CommandKind). Query/question phrasing is checked FIRST so
# a question that happens to contain an action verb ("How should I digitize
# this wing?") is still recognised as a QUERY, not an ACTION — matching
# PROJECT_CONTEXT section 18's own example.
_ACTION_VERBS = [
    (r"\bhow should\b|\bwhat stitch\b|\?\s*$", "query", CommandKind.QUERY),
    (r"\bpunch\b|\bdigitize\b|\btrace\b|\bcreate\b|\bfinish\b", "digitize", CommandKind.ACTION),
    (r"\boptimi[sz]e connectors?\b", "optimize_connectors", CommandKind.ACTION),
    (r"\breduce trims?\b", "reduce_trims", CommandKind.ACTION),
    (r"\bsuggest\b|\brecommend\b", "suggest", CommandKind.SUGGESTION),
    (r"\bshow\b.*\bsequenc", "simulate_sequence", CommandKind.SIMULATION),
    (r"\bsimulate\b|\bpreview\b", "simulate", CommandKind.SIMULATION),
]

# Irregular plurals the naive "add an s" check below would miss, most
# importantly "leaf" -> "leaves" since it's this project's own flagship
# example ("Punch all leaves").
_IRREGULAR_PLURALS = {
    "leaf": "leaves",
    "branch": "branches",
    "flower": "flowers",  # regular, listed for clarity/consistency
}

_QUALITY_CONSTRAINTS = {
    "premium": {"quality_profile": "quality_first"},
    "commercial": {"quality_profile": "commercial"},
    "manual style": {"quality_profile": "manual_style"},
    "minimum thread": {"minimize_thread": True},
    "min thread": {"minimize_thread": True},
}


def _find_category_word(text: str, known_categories: list[str]) -> str | None:
    """Match a category name mentioned in the text against a supplied list
    (typically the categories a human has actually assigned, or a set the
    caller considers plausible) — this function never invents a category
    that wasn't in `known_categories`.
    """
    text_l = text.lower()
    for cat in known_categories:
        cat_l = cat.lower()
        plural = _IRREGULAR_PLURALS.get(cat_l, f"{cat_l}s")
        if cat_l in text_l or plural in text_l:
            return cat
    return None


class CommandParser:
    """Rule-based NL -> StructuredCommand parser.

    `known_categories` should be whatever category vocabulary is currently
    meaningful (e.g. ObjectCategory values plus any custom ones a human has
    used) — passed in rather than hard-coded so the parser doesn't silently
    drift out of sync with the domain model.
    """

    def __init__(self, known_categories: list[str] | None = None):
        from .domain import ObjectCategory

        self.known_categories = known_categories or [c.value for c in ObjectCategory]

    def parse(self, text: str) -> StructuredCommand:
        text_stripped = text.strip()
        intent = "unknown"
        kind = CommandKind.QUERY.value

        for pattern, matched_intent, matched_kind in _ACTION_VERBS:
            if re.search(pattern, text_stripped, re.IGNORECASE):
                intent = matched_intent
                kind = matched_kind.value
                break

        category = _find_category_word(text_stripped, self.known_categories)
        scope = "category" if category else "entire_design"

        constraints: dict = {}
        text_l = text_stripped.lower()
        for phrase, c in _QUALITY_CONSTRAINTS.items():
            if phrase in text_l:
                constraints.update(c)
        if "trim" in text_l and "reduce" in text_l:
            constraints["minimize_trims"] = True
        if "jump" in text_l and ("reduce" in text_l or "minimi" in text_l):
            constraints["minimize_jump_distance"] = True

        return StructuredCommand(
            raw_text=text_stripped,
            intent=intent,
            kind=kind,
            target_category=category,
            scope=scope,
            constraints=constraints,
        )

    def validate(self, command: StructuredCommand, blueprint: ObjectBlueprint) -> StructuredCommand:
        """Check a parsed command against what was ACTUALLY detected.

        Never claims a match that doesn't exist. If `target_category` was
        parsed but no detected object carries that category (remember:
        LocalVisionProvider leaves category as "unknown" until a human or a
        semantic model assigns one — see vision.py), this reports zero
        matches and says so, rather than assuming the objects are there.
        """
        if command.target_category is None:
            command.matched_object_ids = [o.object_id for o in blueprint.objects]
            command.note = f"No specific category requested; {len(command.matched_object_ids)} object(s) in scope."
            return command

        matches = [o for o in blueprint.objects if o.category == command.target_category]
        command.matched_object_ids = [o.object_id for o in matches]
        if not matches:
            command.note = (
                f"I detected 0 confirmed '{command.target_category}' objects. "
                "Would you like me to inspect the reference image again, or "
                "assign categories manually first?"
            )
        else:
            command.note = f"{len(matches)} '{command.target_category}' object(s) matched."
        return command
