"""Parametric VECTOR badge builder (v0.6): rings + circular text + monogram built from true geometry, then run through the
existing technical-blueprint / stitch-plan engines.  This is the 'vector-first' path: no raster contours, no noisy circles.

Objects: outer_border ring (parametric circle + hole), inner_ring, circular text glyphs (outline + counters),
centre monogram glyphs.  Dependencies: border -> text -> inner ring -> monogram (hard sequencing).
"""
from __future__ import annotations

import math

from .circlefit import Circle, circle_polygon
from .curvedtext import layout_text_on_circle
from .domain import EmbroideryObject
from .fonts import glyph_outline
from .vision import ObjectBlueprint


def _bbox(pts):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _obj(oid, name, cat, outer, holes, color, layer, source="vector:badge"):
    o = EmbroideryObject(object_id=oid, name=name, category=cat, geometry=[tuple(p) for p in outer], bbox_mm=_bbox(outer),
                         layer=layer, color_group=color, source=source, holes_mm=[[tuple(p) for p in h] for h in holes], z_order=layer)
    from .geometry import polygon_area
    o.area_mm2 = abs(polygon_area(outer)) - sum(abs(polygon_area(h)) for h in holes)
    return o


def build_vector_badge(size_mm: float = 100.0, font_path: str | None = None, top_text: str = "LOUIS VUITTON",
                       bottom_text: str = "PARIS 1854", monogram: str = "LV", border_mm: float = 3.5, inner_ring_mm: float = 2.0,
                       text_cap_mm: float = 5.0, color: str = "rgb(20,20,20)", text_color: str | None = None,
                       text_mode: str = "BOTTOM_AUTO_FLIP", letter_spacing_mm: float = 0.8):
    """Returns (ObjectBlueprint, dependencies dict, info dict)."""
    from .golden import find_default_font
    font = font_path or find_default_font()
    if not font:
        raise ValueError("FONT_NOT_FOUND: no usable font on this system; pass font_path or set EMBCOPILOT_FONT_DIRS")
    text_color = text_color or color
    c = size_mm / 2.0
    R_out = c - 3.0
    R_in_border = R_out - border_mm
    R_ring_out = R_in_border - text_cap_mm - 5.0
    R_ring_in = R_ring_out - inner_ring_mm
    objs: list[EmbroideryObject] = []
    deps: dict[str, list[str]] = {}

    def ring(oid, name, ro, ri, layer):
        outer = circle_polygon(Circle(c, c, ro, 0, 0, 360)); hole = circle_polygon(Circle(c, c, ri, 0, 0, 360))
        o = _obj(oid, name, "border", outer, [hole], color, layer, "vector:parametric_circle")
        w = ro - ri   # exact: parametric circles, so width is known, not measured from pixels
        o.skeleton_topology = {"endpoints": 0, "branch_points": 0, "closed_loop": True}
        o.width_stats_mm = {"min": w, "median": w, "max": w, "p05": w, "p95": w, "centerline_length_mm": 2 * math.pi * (ro + ri) / 2}
        return o

    border = ring("outer_border", "Outer Border", R_out, R_in_border, 0)
    objs.append(border); deps["outer_border"] = []
    prev = ["outer_border"]
    infos = []
    gid = 0
    for txt, ang, radius in ((top_text, 90.0, R_in_border - 1.0 - text_cap_mm), (bottom_text, 270.0, R_in_border - 1.0)):
        if not txt:
            continue
        ct = layout_text_on_circle(txt, font, radius, text_cap_mm, (c, c), ang, text_mode, letter_spacing_mm=letter_spacing_mm)
        # BOTTOM_AUTO_FLIP puts bottom text INSIDE the baseline: its baseline radius must be the OUTER edge of the band
        infos.append(ct.to_dict() | {"glyphs": len(ct.placements)})
        for p in ct.placements:
            for sh in p.shapes:
                gid += 1
                oid = f"text_{gid:03d}_{p.char}"
                objs.append(_obj(oid, f"Letter {p.char}", "text", sh.outer, sh.holes, text_color, 1))
                deps[oid] = ["outer_border"]
    ring_o = ring("inner_ring", "Inner Ring", R_ring_out, R_ring_in, 2)
    objs.append(ring_o); deps["inner_ring"] = [o for o in deps if o.startswith("text_")] or ["outer_border"]
    if monogram:
        cap = R_ring_in * 0.95
        gl = [glyph_outline(font, ch, cap) for ch in monogram]
        total = sum(g.advance for g in gl) - (gl[0].advance - gl[0].xmax if False else 0)
        x = c - total / 2.0
        for ch, g in zip(monogram, gl):
            for sh in g.shapes:
                tr = lambda p, _x=x: (p[0] + _x, c + cap / 2.0 - p[1])  # noqa: E731 (glyph y-up -> canvas y-down)
                oid = f"mono_{ch}_{len(objs):03d}"
                objs.append(_obj(oid, f"Monogram {ch}", "badge", [tr(p) for p in sh.outer], [[tr(p) for p in h] for h in sh.holes], color, 3))
                deps[oid] = ["inner_ring"]
            x += g.advance
    bp = ObjectBlueprint("vector:badge", (int(size_mm * 10), int(size_mm * 10)), (size_mm, size_mm), objs,
                         {"detected": False, "note": "vector input has no background"}, [])
    info = {"font": font, "radii_mm": {"outer": R_out, "border_inner": R_in_border, "ring_outer": R_ring_out, "ring_inner": R_ring_in},
            "text": infos, "objects": len(objs)}
    return bp, deps, info


def plan_vector_badge(bp, deps, fabric="woven", profile="balanced", overrides=None):
    """Technical blueprint + stitch plan from a vector badge (same engines as the raster path)."""
    from .profiles import get_profile
    from .stitch import build_stitch_plan, validate_stitch_plan
    from .technical import TechnicalBlueprintEngine
    prof = get_profile(profile)
    # parametric rings are satin rings (Input C style) by default; the user can override per object id
    ov = {"outer_border": "satin", "inner_ring": "satin", **(overrides or {})}
    tb, decisions = TechnicalBlueprintEngine().generate(bp, fabric=fabric, production_profile=prof.name, overrides=ov)
    plan, gen = build_stitch_plan(bp.objects, tb, bp.target_size_mm, deps, prof)
    val = validate_stitch_plan(plan, bp.objects, deps)
    return tb, plan, val
