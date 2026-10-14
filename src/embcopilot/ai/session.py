"""DesignSession: the ONLY place AI-approved steps change a design.  State is a small, serialisable dict; every rebuild runs the real engines
(technical blueprint -> stitch plan -> travel/cleanup -> QC v2).  Undo/redo move through stored states; every command is audited."""
from __future__ import annotations

import copy
import dataclasses
import math
import time
import uuid

from ..circlefit import circle_polygon, infer_circle
from ..negative_space import build_protected_zones
from ..profiles import get_profile
from ..qc_v2 import plan_metrics, run_qc
from ..stitch import build_stitch_plan
from ..stitchclean import clean_short_stitches, count_over, split_long_stitches
from ..strategies import StrategyLimits
from ..technical import TechnicalBlueprintEngine
from ..travel import optimize_travel
from .audit import AuditLog

ALIASES = {"outer_border": "outer_ring", "border": "outer_ring", "outer": "outer_ring", "outer_circle": "outer_ring", "inner_border": "inner_ring",
           "inner_circle": "inner_ring", "letters": "text", "lettering": "text", "circular_text": "text", "logo": "monogram", "center_logo": "monogram",
           "emblem": "monogram", "bg": "background"}
UL_MAP = lambda ul: ("double_zigzag" if "double_zigzag" in ul else "zigzag" if "zigzag" in ul else "edge_run" if "edge_run" in ul   # noqa: E731
                     else "center_run" if "center_run" in ul else "tatami" if "tatami" in ul else "none")


def build_roles(objects) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {"outer_ring": [], "inner_ring": [], "text": [], "monogram": [], "background": []}
    rings = []
    for o in objects:
        oid = o.object_id
        if o.category == "background":
            roles["background"].append(oid); continue
        if oid == "outer_border": roles["outer_ring"].append(oid); continue
        if oid == "inner_ring": roles["inner_ring"].append(oid); continue
        if o.category == "text" or oid.startswith("text_"): roles["text"].append(oid); continue
        if oid.startswith("mono_") or o.category == "badge": roles["monogram"].append(oid); continue
        if o.holes_mm and len(o.geometry) >= 12:
            c = infer_circle([tuple(p) for p in o.geometry], rel_tol=0.03)
            h = infer_circle([tuple(p) for p in o.holes_mm[0]], rel_tol=0.03) if len(o.holes_mm[0]) >= 12 else None
            if c and h:
                rings.append((c.r, oid))
    for i, (_, oid) in enumerate(sorted(rings, reverse=True)):
        roles["outer_ring" if i == 0 else "inner_ring"].append(oid)
    return {k: v for k, v in roles.items() if v}


def ring_set_width(obj, width_mm: float) -> str | None:
    """Exact parametric re-width of a circular ring (outer radius kept).  Returns an error string if the object is not a clean ring."""
    if not obj.holes_mm or len(obj.holes_mm) != 1:
        return "not a ring (needs exactly one hole)"
    co = infer_circle([tuple(p) for p in obj.geometry], rel_tol=0.03)
    ci = infer_circle([tuple(p) for p in obj.holes_mm[0]], rel_tol=0.03)
    if not co or not ci or math.hypot(co.cx - ci.cx, co.cy - ci.cy) > 0.03 * co.r:
        return "not a concentric circular ring: width is measured from the artwork and cannot be changed without re-drawing"
    ri = co.r - width_mm
    if ri < 0.5:
        return f"width {width_mm:g} mm leaves no hole (outer radius {co.r:.1f} mm)"
    from ..circlefit import Circle
    obj.geometry = circle_polygon(Circle(co.cx, co.cy, co.r, 0, 0, 360))
    obj.holes_mm = [circle_polygon(Circle(co.cx, co.cy, ri, 0, 0, 360))]
    obj.skeleton_topology = {"endpoints": 0, "branch_points": 0, "closed_loop": True}
    obj.width_stats_mm = {"min": width_mm, "median": width_mm, "max": width_mm, "p05": width_mm, "p95": width_mm,
                          "centerline_length_mm": 2 * math.pi * (co.r + ri) / 2}
    return None


def _rgb(hexc: str) -> str:
    return f"rgb({int(hexc[1:3], 16)},{int(hexc[3:5], 16)},{int(hexc[5:7], 16)})"


@dataclasses.dataclass
class Build:
    plan: object
    objects: list
    tb: object
    qc: object
    zones: list
    metrics: dict
    notes: list


def default_state(fabric: str, profile: str) -> dict:
    return {"fabric": fabric, "profile": profile, "stitch": {}, "width": {}, "density": {}, "pull": {}, "pull_pct": {}, "underlay": {}, "angle": {},
            "color": {}, "deleted": [], "protected": [], "max_stitch_mm": None, "min_stitch_mm": 0.3, "optimize_travel": True,
            "fewer_trims": False, "underlay_spacing_mm": 2.0, "underlay_inset_mm": 0.4}


class DesignSession:
    def __init__(self, blueprint, deps: dict, fabric: str = "woven", profile: str = "balanced", machine: str = "generic_flat",
                 audit: AuditLog | None = None, initial_state: dict | None = None):
        self.bp = blueprint
        self.base_objects = copy.deepcopy(blueprint.objects)
        self.deps = {k: list(v) for k, v in deps.items()}
        self.machine = machine
        self.audit = audit or AuditLog()
        self.state = initial_state or default_state(fabric, profile)
        self.undo_stack: list[tuple[dict, Build, dict]] = []
        self.redo_stack: list[tuple[dict, Build, dict]] = []
        self.history: list[dict] = []
        self.build = self._build(self.state)

    @classmethod
    def from_pipeline(cls, R, deps: dict | None = None, machine: str = "generic_flat", audit: AuditLog | None = None):
        deps = deps if deps is not None else (R.graph.dependencies() if R.graph is not None else {})
        tb = R.technical
        bp = dataclasses.replace(R.blueprint, objects=R.objects)
        return cls(bp, deps, tb.fabric, tb.production_profile, machine, audit)

    # ------------------------------------------------------------------ roles / targets
    def roles(self) -> dict[str, list[str]]:
        return build_roles(self.build.objects)

    def role_names(self) -> list[str]:
        return list(self.roles()) + [o.object_id for o in self.build.objects][:40]

    def resolve(self, target: str | None, build: Build | None = None) -> list[str]:
        b = build or self.build
        ids = [o.object_id for o in b.objects]
        if target in (None, "all"):
            return [i for i in ids if i not in set(build_roles(b.objects).get("background", []))]
        if target.startswith("type:"):
            want = target[5:]
            return [p.object_id for p in b.tb.plans if p.stitch_type == want]
        t = ALIASES.get(target, target)
        if t in ids:
            return [t]
        return list(build_roles(b.objects).get(t, []))

    # ------------------------------------------------------------------ build
    def _build(self, st: dict) -> Build:
        notes: list[str] = []
        deleted = set(st["deleted"])
        objs = [copy.deepcopy(o) for o in self.base_objects if o.object_id not in deleted]
        byid = {o.object_id: o for o in objs}
        for oid, w in st["width"].items():
            if oid in byid:
                err = ring_set_width(byid[oid], w)
                if err:
                    notes.append(f"WIDTH_NOT_APPLIED {oid}: {err}")
        for oid, hx in st["color"].items():
            if oid in byid:
                byid[oid].color_group = _rgb(hx)
        ids = set(byid)
        deps = {k: [d for d in v if d in ids] for k, v in self.deps.items() if k in ids}
        bp = dataclasses.replace(self.bp, objects=objs)
        overrides = {oid: ("satin" if v == "input_c" else v) for oid, v in st["stitch"].items() if oid in ids}
        tb, _ = TechnicalBlueprintEngine().generate(bp, fabric=st["fabric"].lower(), production_profile=st["profile"], overrides=overrides)
        for p in tb.plans:
            oid = p.object_id
            if oid in st["density"]: p.density_mm = st["density"][oid]
            if oid in st["pull"]: p.pull_compensation_mm = st["pull"][oid]
            elif oid in st["pull_pct"]: p.pull_compensation_mm = round(min(0.8, st["pull_pct"][oid] / 100.0 * max(p.width_mm, 0.0)), 3)
            if oid in st["underlay"]: p.underlay = st["underlay"][oid]
            if oid in st["angle"]: p.direction_deg = st["angle"][oid]
        lim = StrategyLimits(max_stitch_mm=st["max_stitch_mm"] or 7.0, min_stitch_mm=st["min_stitch_mm"],
                             underlay_spacing_mm=st["underlay_spacing_mm"], underlay_inset_mm=st["underlay_inset_mm"])
        plan, _gen = build_stitch_plan(objs, tb, bp.target_size_mm, deps, get_profile(st["profile"]), limits=lim)
        zones = build_protected_zones(objs, plan.canvas_mm)
        if st["max_stitch_mm"]:
            plan, n = split_long_stitches(plan, st["max_stitch_mm"])
            if n: notes.append(f"AUTO_SPLIT: split {n} stitch(es) longer than {st['max_stitch_mm']:g} mm")
        if st["optimize_travel"]:
            plan, _ = optimize_travel(plan, zones, self.machine, prefer_fewer_trims=st["fewer_trims"])
        plan, _ = clean_short_stitches(plan, st["min_stitch_mm"])
        qc = run_qc(plan, objs, zones, self.machine)
        m = plan_metrics(plan, zones)
        lim_mm = st["max_stitch_mm"] or 7.0
        m.update(qc_errors=len(qc.errors), qc_warnings=len(qc.by_severity("WARNING")), over_max_stitch=count_over(plan, lim_mm), max_stitch_limit_mm=lim_mm)
        return Build(plan, objs, tb, qc, zones, m, notes)

    # ------------------------------------------------------------------ apply
    def _mutate(self, steps, st: dict, base: Build) -> tuple[list, list]:
        applied, skipped = [], []
        protected = set(st["protected"])
        for s in steps:
            if s.op == "protect":
                ids = self.resolve(s.target, base)
                protected |= set(ids)
        st["protected"] = sorted(protected)
        for s in steps:
            if s.op in ("protect", "qc", "emb_info", "export"):
                applied.append({"step": s.to_dict(), "objects": [], "note": "no design change"}); continue
            p = s.params
            if s.op == "set_fabric":
                st["fabric"] = p["fabric"].lower(); applied.append({"step": s.to_dict(), "objects": ["(all)"]}); continue
            if s.op == "auto_split":
                st["max_stitch_mm"] = p["max_stitch_mm"]; applied.append({"step": s.to_dict(), "objects": ["(all)"]}); continue
            if s.op == "min_stitch":
                st["min_stitch_mm"] = p["min_stitch_mm"]; applied.append({"step": s.to_dict(), "objects": ["(all)"]}); continue
            if s.op == "optimize":
                if "travel" in p["what"]: st["optimize_travel"] = True
                if "fewer_trims" in p["what"]: st["fewer_trims"] = True; st["optimize_travel"] = True
                applied.append({"step": s.to_dict(), "objects": ["(all)"]}); continue
            ids = self.resolve(s.target, base)
            if not ids:
                skipped.append({"step": s.to_dict(), "reason": f"TARGET_NOT_FOUND: '{s.target}' matches no object in this design"}); continue
            hit = [i for i in ids if i not in protected]
            if not hit:
                skipped.append({"step": s.to_dict(), "reason": f"PROTECTED: '{s.target}' was marked 'preserve' and was not changed"}); continue
            for oid in hit:
                if s.op == "set_stitch_type":
                    st["stitch"][oid] = p["stitch_type"]
                    if "width_mm" in p: st["width"][oid] = p["width_mm"]
                elif s.op == "set_width": st["width"][oid] = p["width_mm"]
                elif s.op == "set_density": st["density"][oid] = p["density_mm"]
                elif s.op == "set_pull_comp":
                    if "pull_comp_mm" in p: st["pull"][oid] = p["pull_comp_mm"]; st["pull_pct"].pop(oid, None)
                    else: st["pull_pct"][oid] = p["pull_comp_pct"]; st["pull"].pop(oid, None)
                elif s.op == "set_underlay":
                    st["underlay"][oid] = UL_MAP(p["underlay"])
                    if "underlay_spacing_mm" in p: st["underlay_spacing_mm"] = p["underlay_spacing_mm"]
                    if "underlay_inset_mm" in p: st["underlay_inset_mm"] = p["underlay_inset_mm"]
                elif s.op == "set_angle": st["angle"][oid] = p["angle_deg"]
                elif s.op == "set_color": st["color"][oid] = p["color_hex"]
                elif s.op == "delete_object":
                    if oid not in st["deleted"]: st["deleted"].append(oid)
            note = None
            if s.op == "set_underlay" and "zigzag" in p["underlay"] and "edge_run" in p["underlay"]:
                note = "edge_run + zigzag: the engine's zigzag underlay already includes the edge run, applied as 'zigzag'"
            if s.op == "set_stitch_type" and p["stitch_type"] == "input_c":
                note = "Input C is generated as a satin column/ring (rails from the object's geometry)"
            applied.append({"step": s.to_dict(), "objects": hit, **({"note": note} if note else {})})
        return applied, skipped

    def preview(self, steps) -> dict:
        st = copy.deepcopy(self.state)
        applied, skipped = self._mutate(steps, st, self.build)
        nb = self._build(st)
        return {"applied": applied, "skipped": skipped, "before": self.build.metrics, "after": nb.metrics, "notes": nb.notes}

    def apply(self, steps, prompt: str = "", provider: str = "", model: str = "", risk: str = "low", approval: str = "user") -> dict:
        t0 = time.time()
        st = copy.deepcopy(self.state)
        applied, skipped = self._mutate(steps, st, self.build)
        before = self.build
        changed = st != self.state
        nb = self._build(st) if changed else self.build
        cid = uuid.uuid4().hex[:12]
        entry = {"command_id": cid, "prompt": prompt, "provider": provider, "model": model, "risk": risk, "approval": approval,
                 "applied": applied, "skipped": skipped, "metrics_before": before.metrics, "metrics_after": nb.metrics, "seconds": round(time.time() - t0, 2),
                 "changed": changed}
        if changed:
            self.undo_stack.append((copy.deepcopy(self.state), before, entry))
            self.redo_stack.clear()
            self.state, self.build = st, nb
        self.history.append(entry)
        self.audit.record(command_id=cid, user_prompt=prompt, provider=provider, model=model, risk=risk, approval=approval,
                          steps=[a["step"] for a in applied], skipped=[s["reason"] for s in skipped], before=before.metrics, after=nb.metrics)
        return {**entry, "notes": nb.notes}

    def undo(self) -> dict | None:
        if not self.undo_stack:
            return None
        prev_state, prev_build, entry = self.undo_stack.pop()
        self.redo_stack.append((copy.deepcopy(self.state), self.build, entry))
        self.state, self.build = prev_state, prev_build
        self.audit.record(command_id=entry["command_id"], event="UNDO")
        return self.build.metrics

    def redo(self) -> dict | None:
        if not self.redo_stack:
            return None
        st, b, entry = self.redo_stack.pop()
        self.undo_stack.append((copy.deepcopy(self.state), self.build, entry))
        self.state, self.build = st, b
        self.audit.record(command_id=entry["command_id"], event="REDO")
        return self.build.metrics

    def summary(self) -> dict:
        return {"fabric": self.state["fabric"], "profile": self.state["profile"], "objects": len(self.build.objects), "roles": self.roles(),
                "metrics": self.build.metrics, "state": self.state, "can_undo": bool(self.undo_stack), "can_redo": bool(self.redo_stack),
                "notes": self.build.notes}
