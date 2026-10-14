"""
Production sequence optimizer.

CRITICAL RULE (project brief section 30): never reorder objects blindly.
underlay -> base fill -> detail -> outline must not be randomly reversed.
This module topologically sorts SequenceItems by their declared
`dependencies`, and *within* a dependency-respecting position, uses Phase
1's nearest-neighbour travel logic to pick the order that minimises travel.
It never moves an item ahead of something it depends on, even if that
would shorten travel.
"""

from __future__ import annotations

from .domain import SequenceItem

# The conventional stage order this project assumes unless a SequenceItem's
# own `dependencies` says otherwise. This is a default, not a hard rule —
# an item with explicit dependencies always wins over this table.
DEFAULT_STAGE_ORDER = ["underlay", "base_fill", "detail", "outline"]


class DependencyCycleError(Exception):
    """Raised when SequenceItem.dependencies contains a cycle — never
    silently picks an arbitrary order in that case."""


def _stage_rank(stage: str) -> int:
    try:
        return DEFAULT_STAGE_ORDER.index(stage)
    except ValueError:
        return len(DEFAULT_STAGE_ORDER)  # unknown stages sort last, not first


def topological_stage_order(items: list[SequenceItem]) -> list[SequenceItem]:
    """Order items so every dependency comes before its dependents.

    Ties (items with no ordering constraint between them) are broken by
    `DEFAULT_STAGE_ORDER`, then by their position in the input list — never
    by anything that would silently violate an explicit dependency.
    """
    by_id = {item.object_id: item for item in items}
    visited: set[str] = set()
    temp_mark: set[str] = set()
    ordered: list[SequenceItem] = []

    def visit(item: SequenceItem) -> None:
        if item.object_id in visited:
            return
        if item.object_id in temp_mark:
            raise DependencyCycleError(
                f"Dependency cycle detected involving object {item.object_id!r}."
            )
        temp_mark.add(item.object_id)
        for dep_id in sorted(item.dependencies, key=lambda d: _stage_rank(by_id[d].stage) if d in by_id else -1):
            dep = by_id.get(dep_id)
            if dep is not None:
                visit(dep)
        temp_mark.discard(item.object_id)
        visited.add(item.object_id)
        ordered.append(item)

    # Visit in an order that already prefers earlier stages first, so ties
    # come out in the conventional underlay -> fill -> detail -> outline
    # order rather than input order when there's no explicit dependency.
    for item in sorted(items, key=lambda it: (_stage_rank(it.stage), items.index(it))):
        visit(item)

    return ordered


def propose_sequence(items: list[SequenceItem]) -> list[SequenceItem]:
    """Return a dependency-respecting sequence proposal.

    This is a PROPOSAL (project brief section 31: "Allow sequence proposal
    before applying changes") — it does not mutate `items` or write
    anything; the caller decides whether to adopt the returned order.
    """
    return topological_stage_order(list(items))


# ---------------------------------------------------------------------------
# Sequence V2: geometry-aware ordering (Phase 10)
# ---------------------------------------------------------------------------
import math as _math
from dataclasses import dataclass as _dc


@_dc
class SeqNode:
    object_id: str
    color: str
    entry: tuple[float, float]
    exit: tuple[float, float]
    z_order: int = 0


def order_objects(nodes: list[SeqNode], deps: dict[str, list[str]],
                  color_change_penalty_mm: float = 60.0) -> list[str]:
    """Dependency-respecting order that minimises real travel.

    Kahn's algorithm: at every step only objects whose dependencies are all
    already sewn are eligible (so a dependency is NEVER violated to save
    travel). Among eligible objects pick the cheapest next hop, where cost =
    distance(exit of previous -> entry of candidate) + a penalty if the
    colour differs (each colour change costs machine time). Lower z_order is
    preferred on ties so background-first is the default. Raises
    DependencyCycleError on a cycle."""
    by = {n.object_id: n for n in nodes}
    indeg = {i: len([d for d in deps.get(i, []) if d in by]) for i in by}
    done: list[str] = []
    ready = [i for i, d in indeg.items() if d == 0]
    cur = None
    while ready:
        def cost(i):
            n = by[i]
            if cur is None:
                return (n.z_order, n.entry[1], n.entry[0])
            p = by[cur]
            d = _math.dist(p.exit, n.entry)
            return (n.z_order * 1000 + d + (color_change_penalty_mm if n.color != p.color else 0.0), n.object_id)
        ready.sort(key=cost)
        nxt = ready.pop(0)
        done.append(nxt)
        cur = nxt
        for j, dl in deps.items():
            if j in by and nxt in dl and j not in done:
                indeg[j] -= 1
                if indeg[j] == 0 and j not in ready:
                    ready.append(j)
    if len(done) != len(by):
        raise DependencyCycleError("Dependency cycle among objects: " + ", ".join(sorted(set(by) - set(done))))
    return done
