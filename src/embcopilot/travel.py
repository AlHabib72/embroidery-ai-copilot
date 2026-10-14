"""Travel optimiser (v0.6): applies the production-aware trim policy + protected-zone routing to a StitchPlan.

For every thread-bearing JUMP (a JUMP not preceded by a TRIM):
  1. crosses a protected zone  -> try a detour (grid A*); if no detour exists -> TRIM + jump
  2. short visible travel       -> keep as jump
  3. long visible travel        -> insert TRIM before the jump (if the machine can trim)
The input plan is never mutated and every change is itemised (original strategy / repair / reason / impact).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from .machine import MachineProfile, TravelContext, decide_travel, get_machine
from .negative_space import ProtectedTravelZone, find_protected_crossings, route_around
from .stitch import Stitch


@dataclass
class TravelReport:
    decisions: list[dict] = field(default_factory=list)
    trims_added: int = 0
    rerouted: int = 0
    kept: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"trims_added": self.trims_added, "rerouted": self.rerouted, "kept_as_jump": self.kept,
                "warnings": self.warnings, "decisions": self.decisions[:200],
                "original_strategy": "untrimmed jump", "repair_strategy": "production-aware trim policy + protected-zone detour",
                "reason": "thread dragged across visible fabric / protected region",
                "impact": f"+{self.trims_added} trims, {self.rerouted} detours"}


def optimize_travel(plan, zones: list[ProtectedTravelZone] | None = None, machine: MachineProfile | str | None = None,
                    fabric_stretch: float = 0.0, thread_visibility: float = 0.5, prefer_fewer_trims: bool = False):
    m = get_machine(machine)
    detour_factor, detour_min = (6.0, 45.0) if prefer_fewer_trims else (2.5, 20.0)   # user asked for fewer trims: accept longer hidden detours
    zones = [z for z in (zones or []) if not z.allow_override]
    rep = TravelReport()
    st = plan.stitches
    # --- pre-pass: sewn connector runs (edge/hidden travel) that graze a protected zone become ONE trimmed move
    if zones and m.trim_capability:
        bad = {c.stitch_index for c in find_protected_crossings(plan, zones) if c.command == "STITCH"}
        if bad:
            res: list[Stitch] = []
            k = 0
            conn = lambda q: q.command == "STITCH" and q.part.startswith("connector")  # noqa: E731
            while k < len(st):
                if conn(st[k]):
                    e = k
                    while e + 1 < len(st) and conn(st[e + 1]):
                        e += 1
                    if any(x in bad for x in range(k, e + 1)) and res:
                        prev = res[-1]
                        res.append(Stitch(prev.x, prev.y, "TRIM", prev.color, prev.object_id, prev.layer, "connector:trim_policy"))
                        res.append(Stitch(st[e].x, st[e].y, "JUMP", st[e].color, st[e].object_id, st[e].layer, "connector:trimmed_travel"))
                        rep.trims_added += 1
                        rep.decisions.append({"at": k, "action": "sewn_connector_to_trimmed_jump", "stitches_replaced": e - k + 1})
                        k = e + 1
                        continue
                res.append(st[k]); k += 1
            plan = copy.copy(plan); plan.stitches = res; st = res
    crossing_idx = {c.stitch_index for c in find_protected_crossings(plan, zones)} if zones else set()
    out: list[Stitch] = []
    i = 0
    while i < len(st):
        s = st[i]
        if s.command != "JUMP" or i == 0:
            out.append(s); i += 1; continue
        # head of a run of consecutive JUMP records = one travel move
        prev = out[-1]
        already_trimmed = any(q.command == "TRIM" for q in out[-3:] if True) and out[-1].command in ("TRIM", "JUMP")
        j = i
        while j + 1 < len(st) and st[j + 1].command == "JUMP":
            j += 1
        dest = st[j]
        d = math.dist((prev.x, prev.y), (dest.x, dest.y))
        if already_trimmed or prev.command in ("COLOR_CHANGE", "STOP", "TRIM"):
            out.extend(st[i:j + 1]); i = j + 1; continue
        crosses = i in crossing_idx or any(k in crossing_idx for k in range(i, j + 1))
        same_obj = prev.object_id == dest.object_id
        # a jump INSIDE one fill object stays inside that object's region and is covered by its own later rows
        hidden = same_obj and (dest.part or "").startswith("connector:intra_object")
        base = decide_travel(TravelContext(d, same_obj, hidden, False, fabric_stretch, thread_visibility), m)
        dec = base
        if crosses:
            # a long visible move is trimmed anyway (no thread is dragged, so crossing the zone is harmless);
            # only SHORT thread-bearing travel (or machines that cannot trim) needs a detour
            dec = base if (base["action"] == "trim_jump" and m.trim_capability) else decide_travel(
                TravelContext(d, same_obj, False, True, fabric_stretch, thread_visibility), m)
        if dec["action"] == "route_around":
            path = route_around((prev.x, prev.y), (dest.x, dest.y), zones, 0.8, plan.canvas_mm)
            if path and len(path) >= 2:
                detour = sum(math.dist(path[k], path[k + 1]) for k in range(len(path) - 1))
                # a long detour drags more thread than it saves: above 2.5x the straight run (and > 20 mm) trim instead
                if detour <= max(detour_factor * d, detour_min) or not m.trim_capability:
                    for w in path[1:-1]:
                        out.append(Stitch(w[0], w[1], "JUMP", dest.color, dest.object_id, dest.layer, "connector:detour"))
                    out.append(dest)
                    rep.rerouted += 1
                    rep.decisions.append({"at": i, "action": "detour", "straight_mm": round(d, 1), "path_mm": round(detour, 1)})
                    i = j + 1; continue
            dec = {"action": "trim_jump", "reason": "no acceptable detour around protected zone; trim so no thread crosses it"}
        if dec["action"] == "trim_jump" and m.trim_capability:
            out.append(Stitch(prev.x, prev.y, "TRIM", prev.color, prev.object_id, prev.layer, "connector:trim_policy"))
            rep.trims_added += 1
            rep.decisions.append({"at": i, "action": "trim_jump", "distance_mm": round(d, 1), "reason": dec["reason"]})
        else:
            rep.kept += 1
            if dec.get("warning") and dec["warning"] not in rep.warnings:
                rep.warnings.append(dec["warning"])
        out.extend(st[i:j + 1]); i = j + 1
    new = copy.copy(plan)
    new.stitches = out
    return new, rep
