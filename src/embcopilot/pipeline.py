"""End-to-end pipeline: reference image -> vision -> occlusion-aware blueprint
-> semantics -> technical blueprint -> stitch geometry -> validation ->
connector/sequence -> simulation -> comparison -> QC -> confidence/risk ->
action plan (never executed here). Every stage's output is written as a
numbered debug artifact. Fails safe: if vision yields nothing, nothing
downstream is generated."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from . import __version__
from .blueprint import apply_blueprint, build_blueprint, carry_occlusions, merge_continuations
from .compare_geo import compare_geometry
from .confidence import assess_object
from .domain import ConfidenceThresholds
from .profiles import get_profile
from .semantics import apply_semantics
from .stitch import build_stitch_plan, to_dst_design, validate_stitch_plan
from .technical import TechnicalBlueprintEngine
from .vision import LocalVisionProvider


@dataclass
class PipelineResult:
    blueprint: object
    objects: list
    graph: object
    technical: object
    plan: object
    validation: object
    comparison: list
    comparison_global: dict
    assessments: list
    action_plan: list
    qc: dict
    timings: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    stopped: str | None = None
    input_kind: str = "image"
    colorway: object = None
    hoop_check: dict | None = None
    vision_mode: str = "Standard"
    warnings: list = field(default_factory=list)
    repair: object = None


def _j(obj):
    return json.loads(json.dumps(obj, default=lambda o: o.to_dict() if hasattr(o, "to_dict") else o.__dict__))


def build_action_plan(objects, technical, plan) -> list:
    """Structured, allow-listed actions with REAL geometry. Objects whose
    geometry is unavailable are marked GEOMETRY_UNAVAILABLE and get no
    create_geometry action (never a placeholder)."""
    from .automation.schema import ActionRequest

    plans = {p.object_id: p for p in technical.plans}
    acts = []
    for oid in plan.order:
        o = next(x for x in objects if x.object_id == oid)
        tp = plans[oid]
        pts = o.centerline_mm if tp.stitch_type in ("run", "triple_run") and o.centerline_mm else o.geometry
        if not pts:
            acts.append(ActionRequest("create_geometry", oid, {"status": "GEOMETRY_UNAVAILABLE"}, 0.0, "high_risk", True))
            continue
        conf = min(v.value for v in o.confidence.values()) if o.confidence else 0.0
        acts.append(ActionRequest("select_tool", tp.stitch_type, {"object": oid}, conf, "low_risk", True))
        acts.append(ActionRequest("create_geometry", oid, {"points_mm": [(round(x, 2), round(y, 2)) for x, y in pts],
                                                          "holes": len(o.holes_mm)}, conf, "medium_risk", True))
        acts.append(ActionRequest("set_properties", oid, {"density_mm": tp.density_mm, "underlay": tp.underlay,
                                                        "pull_compensation_mm": tp.pull_compensation_mm,
                                                        "angle_deg": tp.direction_deg}, conf, "medium_risk", True))
    return acts


def _vector_blueprint(path, kind, target_mm, fit=False):
    """SVG / PDF-compatible AI -> ObjectBlueprint without rasterising the geometry (a preview raster is only for display)."""
    import cv2
    import numpy as np
    from .geometry import rasterize
    from .vector import objects_from_file
    from .vision import ObjectBlueprint

    doc, objs = objects_from_file(path, kind, target_mm, fit)
    W, H = doc.width_mm, doc.height_mm
    sc = min(8.0, 800.0 / max(W, H))
    size = (int(W * sc) + 2, int(H * sc) + 2)
    prev = np.full((size[1], size[0], 3), 255, np.uint8)
    fg = np.zeros((size[1], size[0]), np.uint8)
    for o in objs:
        m = rasterize(o.geometry, o.holes_mm, sc, size)
        try:
            r, g, b = (int(v) for v in o.color_group[4:-1].split(","))
        except Exception:
            r, g, b = 128, 128, 128
        prev[m > 0] = (b, g, r)
        fg |= m
    bp = ObjectBlueprint(source_image=path, image_size_px=size, target_size_mm=(W, H), objects=objs,
                         background={"mode": "vector", "confidence": 1.0}, warnings=list(doc.warnings))
    bp.debug_images = {"original": prev, "foreground_mask": fg, "segmentation": prev}
    return bp, (W, H)


def run_pipeline(image_path: str, target_mm: tuple[float, float], profile: str = "balanced", fabric: str = "woven",
                 out_dir: str | None = None, thresholds: ConfidenceThresholds | None = None,
                 overrides: dict | None = None, brand: str | None = None, colorway_mode: str = "nearest",
                 hoop: str | None = None, thread_db=None, progress=None, segmentation: str = "standard",
                 prompts: dict | None = None, vector_fit: bool = False) -> PipelineResult:
    import cv2

    from .analyze import analyze
    from .simulate import html_player, render

    prof = get_profile(profile)
    T: dict[str, float] = {}
    t0 = time.time()

    def lap(name, t):
        T[name] = round(time.time() - t, 3)

    def step(stage, pct):
        if progress:
            progress(stage, pct)

    from .inputs import sniff
    sn = sniff(image_path)
    if not sn.ok:
        return PipelineResult(None, [], None, None, None, None, [], {}, [], [], {}, T, stopped=f"INPUT_REJECTED: {sn.problem}")
    step("PREPROCESS", 8)
    t = time.time()
    input_kind = "raster"
    vision_mode = "Standard"
    if sn.kind in ("svg", "ai"):
        from .vector import VectorError
        try:
            bp, target_mm = _vector_blueprint(image_path, sn.kind, target_mm, vector_fit)
        except VectorError as e:
            return PipelineResult(None, [], None, None, None, None, [], {}, [], [], {}, T, stopped=str(e), input_kind=sn.kind)
        input_kind, vision_mode = "vector", "Vector (no segmentation)"
    elif sn.kind in ("png", "jpg", "webp"):
        from .segmentation import Cancelled, SegmentationUnavailable, get_provider, objects_from_masks
        prov, vision_mode, seg_warn = get_provider(segmentation)
        if prov.name == "SAM2":
            try:
                import cv2
                ms = prov.masks(image_path, **(prompts or {}))
                bp = objects_from_masks(ms, cv2.imread(image_path), target_mm)
            except (SegmentationUnavailable, Cancelled, Exception) as e:  # noqa: BLE001 - model failures must never crash the app
                seg_warn.append(f"SAM2_FAILED: {e}; fell back to the standard Vision Engine")
                vision_mode = "Standard (SAM2 failed)"
                bp = LocalVisionProvider().analyze(image_path, target_mm)
        else:
            bp = LocalVisionProvider().analyze(image_path, target_mm)
        bp.warnings += seg_warn
    else:
        return PipelineResult(None, [], None, None, None, None, [], {}, [], [], {}, T,
                              stopped=f"INPUT_KIND_NOT_DIGITIZABLE: {sn.kind.upper()} is a machine/native file, not artwork; use QC/inspect on it instead")
    lap("vision", t); step("SEGMENT", 20)
    if not bp.objects:
        return PipelineResult(bp, [], None, None, None, None, [], {}, [], [], {}, T, stopped="VISION_FOUND_NO_OBJECTS: nothing generated")
    t = time.time()
    g0 = build_blueprint(bp.objects, target_mm)
    objs = merge_continuations(bp.objects, g0)
    merged_ids = {o.object_id for o in objs if any(r["kind"] == "MERGED_FROM" for r in o.relations)}
    g = build_blueprint(objs, target_mm)
    carry_occlusions(g0, g, objs)
    apply_blueprint(objs, g)
    apply_semantics(objs, target_mm, {c: p for c, p in g.parent.items() if p})
    lap("blueprint+semantics", t)
    bp.objects = objs

    t = time.time(); tb, decisions = TechnicalBlueprintEngine().generate(bp, fabric=fabric, production_profile=prof.name, overrides=overrides); lap("technical", t)
    step("TECHNICAL_PLAN", 50)
    deps = g.dependencies()
    t = time.time(); plan, gen = build_stitch_plan(objs, tb, target_mm, deps, prof); lap("stitch+sequence+connectors", t)
    step("STITCH_GENERATION", 70)
    t = time.time(); val = validate_stitch_plan(plan, objs, deps); lap("validate", t)
    t = time.time(); cmp_objs, cmp_glob = compare_geometry(objs, plan, bp.debug_images.get("foreground_mask")); lap("compare", t)

    # QC on the real generated design (existing DST QC engine) — only if export is legal
    qc = {"geometry_validation": val.to_dict(), "dst_qc": None}
    design = None
    if val.status != "ERROR" or all(f.code in ("OBJECT_NOT_GENERATED",) for f in val.findings if f.severity == "ERROR"):
        try:
            design = to_dst_design(plan)
            a = analyze(design)
            qc["dst_qc"] = {"score": getattr(a, "score", None), "stitches": a.stitches, "jumps": a.jumps, "colors": a.colors,
                            "width_mm": round(a.width_mm, 1), "height_mm": round(a.height_mm, 1),
                            "issues": [{"severity": i.severity, "title": i.title} for i in a.issues]}
        except ValueError as e:
            qc["dst_qc"] = {"error": str(e)}
    th = thresholds or ConfidenceThresholds()
    assessments = [assess_object(o, plan.object_status.get(o.object_id, {}), val.findings, o.object_id in merged_ids, th, prof.min_feature_mm) for o in objs]
    actions = build_action_plan(objs, tb, plan)
    T["total"] = round(time.time() - t0, 3)
    res = PipelineResult(bp, objs, g, tb, plan, val, cmp_objs, cmp_glob, assessments, actions, qc, T,
                         input_kind=input_kind, vision_mode=vision_mode, warnings=list(bp.warnings))
    # thread colourway (never changes geometry) + hoop check
    from .threads import ThreadDatabase, build_colorway
    db = thread_db or ThreadDatabase.generic()
    used = []
    for oid in plan.order:
        c = next(o.color_group for o in objs if o.object_id == oid)
        if c not in used:
            used.append(c)
    try:
        res.colorway = build_colorway(used, db, brand if brand in db.brands else None, colorway_mode)
        if brand and brand not in db.brands:
            res.warnings.append(f"THREAD_BRAND_NOT_LOADED: {brand!r}; used all loaded palettes ({', '.join(db.brands)}). Import your licensed palette CSV to use it.")
    except ValueError as e:
        res.warnings.append(str(e))
    if hoop:
        from .fabrics import HOOPS, check_hoop
        b = plan.stats()
        xs = [s_.x for s_ in plan.stitches if s_.command == "STITCH"]; ys = [s_.y for s_ in plan.stitches if s_.command == "STITCH"]
        res.hoop_check = check_hoop(max(xs) - min(xs), max(ys) - min(ys), HOOPS[hoop]) if xs else None
    step("QC", 85)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        def w(name, data):
            path = os.path.join(out_dir, name)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(_j(data), fh, indent=2)
            res.artifacts.append(path)
        def wi(name, img):
            path = os.path.join(out_dir, name); cv2.imwrite(path, img); res.artifacts.append(path)
        di = bp.debug_images
        wi("01_reference.png", di["original"]); wi("02_background_removed.png", di["foreground_mask"]); wi("03_segmentation.png", di["segmentation"])
        w("04_objects.json", {"version": __version__, "background": bp.background, "warnings": bp.warnings, "objects": [o.to_dict() for o in objs]})
        w("05_object_blueprint.json", g.to_dict())
        w("06_technical_blueprint.json", tb.to_dict())
        w("07_stitch_geometry.json", plan.to_dict(include_stitches=True))
        w("08_sequence.json", {"order": plan.order, "dependencies": deps, "z_order": g.z_order})
        w("09_connectors.json", plan.connectors)
        if res.colorway:
            w("06b_thread_mapping.json", res.colorway.to_dict())
        w("10_qc_report.json", {**qc, "hoop": res.hoop_check, "warnings": res.warnings, "vision_mode": vision_mode, "input_kind": input_kind, "comparison": [c.to_dict() for c in cmp_objs], "comparison_global": cmp_glob,
                                "assessments": [a.to_dict() for a in assessments], "timings_s": T})
        wi("11_simulation.png", render(plan, "path")); wi("11b_simulation_thread.png", render(plan, "thread"))
        w("12_action_plan.json", {"MODE": "PLAN ONLY — nothing executed", "actions": [a.to_dict() for a in actions]})
        if design is not None and val.status != "ERROR":
            from .dst import write_dst
            p = os.path.join(out_dir, "design.dst"); write_dst(design, p, "COPILOT"); res.artifacts.append(p)
        p = os.path.join(out_dir, "playback.html")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(html_player(plan, objs))
        res.artifacts.append(p)
    return res
