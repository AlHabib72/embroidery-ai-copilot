"""
Semantic object classification (Phase C) — HEURISTIC, geometry + colour +
containment only. It is NOT a trained model and cannot recognise a "wing" or
"beak" from shape alone; those classes appear only as low-confidence
alternatives when a real geometric cue exists (e.g. EYE = small round region
contained inside a larger one). Every result carries the primary type, its
confidence, ranked alternatives, and a human-readable reason / ambiguity note.

The taxonomy is an open registry: `register_semantic_type("FEATHER")`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .domain import EmbroideryObject
from .geometry import bbox as _bbox, polygon_area
from .knowledge import principal_axis_deg

SEMANTIC_TYPES: set[str] = {
    "BODY", "HEAD", "WING", "TAIL", "BEAK", "EYE", "LEG", "FOOT", "STEM", "LEAF", "FLOWER",
    "PETAL", "BRANCH", "OUTLINE", "TEXT", "SMALL_DETAIL", "BACKGROUND", "DECORATION", "UNKNOWN",
}


def register_semantic_type(name: str) -> None:
    SEMANTIC_TYPES.add(name.upper())


@dataclass
class SemanticResult:
    semantic_type: str
    confidence: float
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    reason: str = ""
    ambiguity: str = ""


def _hue_family(color_group: str) -> str:
    """'rgb(r,g,b)' (RGB order as written by vision) -> coarse family."""
    try:
        r, g, b = (int(v) for v in color_group[4:-1].split(","))
    except Exception:
        return "unknown"
    mx, mn = max(r, g, b), min(r, g, b)
    if mx < 60:
        return "dark"
    if mx - mn < 25:
        return "neutral"
    if g >= r and g >= b:
        return "green"
    if r >= g and r >= b:
        return "warm" if g < r * 0.85 or b < r * 0.85 else "warm"
    return "cool"


def _features(o: EmbroideryObject, canvas_mm: tuple[float, float]) -> dict:
    w, h = o.width_mm, o.height_mm
    ws = o.width_stats_mm or {}
    med = ws.get("median", min(w, h))
    length = ws.get("centerline_length_mm", max(w, h))
    area = o.area_mm2 if o.area_mm2 is not None else polygon_area(o.geometry)
    elong = (length / med) if med > 1e-6 else 1.0
    perim = sum(math.dist(o.geometry[i], o.geometry[(i + 1) % len(o.geometry)]) for i in range(len(o.geometry))) if len(o.geometry) > 2 else 0.0
    circ = (4 * math.pi * polygon_area(o.geometry) / perim**2) if perim > 0 else 0.0
    cover = (w * h) / max(canvas_mm[0] * canvas_mm[1], 1e-6)
    _ang, var_ratio = principal_axis_deg(o.geometry)
    aspect = math.sqrt(min(var_ratio, 400.0))  # axis-length ratio of the outline
    return dict(aspect=aspect, w=w, h=h, med=med, length=length, area=area, elong=elong, circ=circ,
                cover=cover, holes=len(o.holes_mm), family=_hue_family(o.color_group),
                width_var=(ws.get("p95", med) - ws.get("p05", med)) / max(med, 1e-6) if ws else 0.0,
                branches=(o.skeleton_topology or {}).get("branch_points", 0),
                closed_loop=bool((o.skeleton_topology or {}).get("closed_loop")))


def classify_objects(objects: list[EmbroideryObject], canvas_mm: tuple[float, float],
                     containment: dict[str, str] | None = None) -> list[SemanticResult]:
    """Classify every object. `containment` maps child_id -> container_id
    (from blueprint.build_relations) and is what enables EYE/CENTER cues."""
    containment = containment or {}
    byid = {o.object_id: o for o in objects}
    feats = {o.object_id: _features(o, canvas_mm) for o in objects}
    out: list[SemanticResult] = []
    text_like = _text_cluster(objects, feats)

    for o in objects:
        f = feats[o.object_id]
        scores: dict[str, tuple[float, str]] = {}

        def add(t, s, why):
            if t in SEMANTIC_TYPES and (t not in scores or s > scores[t][0]):
                scores[t] = (max(0.0, min(0.97, s)), why)

        if f["cover"] > 0.85 and f["holes"] == 0:
            add("BACKGROUND", 0.85, f"covers {f['cover']*100:.0f}% of the canvas")
        thin = f["med"] < 0.06 * min(canvas_mm)
        if f["elong"] >= 6 and thin and not f["closed_loop"]:
            base = 0.72 + min(0.15, (f["elong"] - 6) / 60)
            if f["family"] in ("green", "dark", "warm"):
                base += 0.04
            add("STEM", base, f"thin (~{f['med']:.1f}mm) and strongly elongated (ratio {f['elong']:.0f}) with a centerline")
            add("BRANCH", base - 0.10 + (0.15 if f["branches"] >= 1 else 0), "thin elongated" + (f", {f['branches']} branch point(s)" if f["branches"] else ""))
            add("OUTLINE", 0.35, "thin elongated shape could also be a contour line")
            if f["holes"] >= 1:  # a stem has no counters; a thin holed shape looks like a glyph
                for t in ("STEM", "BRANCH"):
                    if t in scores:
                        scores[t] = (scores[t][0] - 0.30, scores[t][1] + "; reduced: shape has a hole/counter")
                add("TEXT", 0.55, "thin strokes with an enclosed counter look glyph-like")
        if 1.6 <= f["aspect"] < 6 and f["circ"] < 0.9 and f["holes"] == 0 and f["med"] >= 0.03 * min(canvas_mm) and f["elong"] < 12:
            g = 0.50 + min(0.08, (f["aspect"] - 1.6) / 20) + min(0.06, f["width_var"] / 10) + (0.25 if f["family"] == "green" else 0.0) + min(0.08, f["width_var"] / 8)
            add("LEAF", g, f"lens-like: axis ratio {f['aspect']:.1f}, width varies {f['width_var']*100:.0f}% along axis"
                + (", green" if f["family"] == "green" else ""))
            add("PETAL", g - 0.20 + (0.12 if f["family"] == "warm" else 0), "petal/leaf are geometrically alike")
            add("WING", 0.22, "elongated lens can be a wing (no anatomy cue available)")
        if f["holes"] >= 1 and f["closed_loop"] and f["aspect"] < 2.5 and f["area"] > 0:
            add("DECORATION", 0.62, "closed ring/annulus region (badge or frame)")
        if f["holes"] >= 1 and f["cover"] < 0.06 and not f["closed_loop"]:
            add("TEXT", 0.30 + (0.35 if o.object_id in text_like else 0), "small glyph-like region with counter(s)")
        if o.object_id in text_like:
            add("TEXT", 0.68, "part of a row of similarly sized glyph-like regions")
        cont = containment.get(o.object_id)
        if cont:
            parent = byid[cont]
            pa = feats[cont]["area"]
            if pa > 0 and f["area"] / pa < 0.08 and f["circ"] > 0.6:
                add("EYE", 0.50 + (0.15 if f["family"] == "dark" else 0), "small compact round region fully inside a larger one")
                add("SMALL_DETAIL", 0.40, "small region inside another")
            elif f["area"] / max(pa, 1e-6) >= 0.08:
                add("DECORATION", 0.35, "sizeable region nested inside another")
        if f["area"] < 0.0015 * canvas_mm[0] * canvas_mm[1]:
            add("SMALL_DETAIL", 0.60, f"tiny region ({f['area']:.1f} mm²)")
        if not scores:
            if f["circ"] > 0.8 and f["cover"] > 0.03:
                add("BODY", 0.40, "large compact region (weak cue only)")
            add("UNKNOWN", 0.30, "no geometric cue matched any semantic class")

        ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
        top_t, (top_s, top_why) = ranked[0]
        alts = [(t, s) for t, (s, _) in ranked[1:4]]
        amb = ""
        if alts and top_s - alts[0][1] < 0.15:
            amb = f"{top_t} vs {alts[0][0]} differ by only {(top_s-alts[0][1])*100:.0f} points — needs human review"
        # never claim more than a heuristic can: cap
        out.append(SemanticResult(top_t, round(top_s, 3), [(t, round(s, 3)) for t, s in alts], top_why, amb))
    return out


def _text_cluster(objects, feats) -> set[str]:
    """>=3 objects of similar height, roughly on one baseline, glyph-sized."""
    cand = [o for o in objects if feats[o.object_id]["cover"] < 0.08 and 0.4 < feats[o.object_id]["h"] / max(feats[o.object_id]["w"], 1e-6) < 4]
    groups: list[list] = []
    for o in sorted(cand, key=lambda o: o.bbox_mm[0]):
        for g in groups:
            ref = g[0]
            if abs(o.height_mm - ref.height_mm) / max(ref.height_mm, 1e-6) < 0.25 and abs(o.bbox_mm[3] - ref.bbox_mm[3]) < 0.25 * ref.height_mm \
               and o.color_group == ref.color_group:
                g.append(o)
                break
        else:
            groups.append([o])
    return {o.object_id for g in groups if len(g) >= 3 for o in g}


def apply_semantics(objects: list[EmbroideryObject], canvas_mm, containment=None) -> list[SemanticResult]:
    res = classify_objects(objects, canvas_mm, containment)
    for o, r in zip(objects, res):
        o.category = r.semantic_type.lower()
        o.semantic_alternatives = [{"type": t, "confidence": c} for t, c in r.alternatives]
        o.semantic_reason = r.reason + (f" | AMBIGUOUS: {r.ambiguity}" if r.ambiguity else "")
        o.confidence["semantic"] = _conf(r.confidence, "heuristic semantic score, not a trained classifier")
    return res


def _conf(v, why):
    from .domain import Confidence
    return Confidence(v, why)
