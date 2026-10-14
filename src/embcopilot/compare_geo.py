"""Geometric + stitch comparison (Phase 12): reference regions vs the area
actually covered by generated stitches. Rasterised, tolerance based -
embroidery is not a pixel copy, so we report coverage / spill / centroid /
size differences per object, never pixel equality."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .domain import EmbroideryObject
from .geometry import rasterize
from .stitch import STITCH, StitchPlan


@dataclass
class ObjectComparison:
    object_id: str
    status: str  # MATCH WARNING MISSING EXTRA DISTORTED
    coverage: float  # share of reference region covered by its stitches
    spill: float  # stitched area outside the region (relative to region area)
    centroid_shift_mm: float
    size_ratio: float
    notes: list[str] = field(default_factory=list)
    contour_chamfer_mm: float | None = None   # mean symmetric distance reference contour <-> stitched-coverage contour
    contour_max_mm: float | None = None       # worst-case (Hausdorff-like) distance
    holes_reference: int = 0
    holes_stitched: int = 0
    topology_ok: bool = True

    def to_dict(self):
        return self.__dict__.copy()


def _coverage_mask(plan: StitchPlan, scale, size, object_id=None, thread_mm=0.6):
    import cv2

    m = np.zeros((size[1], size[0]), np.uint8)
    prev = None
    t = max(1, int(round(thread_mm * scale)))
    for s in plan.stitches:
        if s.command == STITCH and prev is not None and prev.command == STITCH and (object_id is None or s.object_id == object_id) and prev.object_id == s.object_id:
            cv2.line(m, (int(round(prev.x * scale)), int(round(prev.y * scale))), (int(round(s.x * scale)), int(round(s.y * scale))), 255, t)
        if s.command != "TRIM":
            prev = s
    return m > 0


def _contour_topology(oc, o, cov_m, scale):
    import cv2
    from .geometry import dist_point_to_polyline, resample_polyline
    m = cv2.morphologyEx(cov_m.astype(np.uint8) * 255, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(1.2 * scale) | 1,) * 2))
    cs, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None or not cs:
        return
    hier = hier[0]
    outer = [i for i, h in enumerate(hier) if h[3] == -1]
    big = max(outer, key=lambda i: cv2.contourArea(cs[i]))
    holes = [i for i, h in enumerate(hier) if h[3] == big and cv2.contourArea(cs[i]) / (scale * scale) >= 1.5]
    oc.holes_reference, oc.holes_stitched = len(o.holes_mm), len(holes)
    oc.topology_ok = oc.holes_reference == oc.holes_stitched
    sc = [(float(p[0][0]) / scale, float(p[0][1]) / scale) for p in cs[big][:: max(1, len(cs[big]) // 300)]]
    ref = resample_polyline(o.geometry + [o.geometry[0]], 0.8)
    a = [dist_point_to_polyline(p, o.geometry, True) for p in sc]
    b = [dist_point_to_polyline(p, sc, True) for p in ref]
    oc.contour_chamfer_mm = round((sum(a) / len(a) + sum(b) / len(b)) / 2, 3)
    oc.contour_max_mm = round(max(max(a), max(b)), 3)


def compare_geometry(objects: list[EmbroideryObject], plan: StitchPlan, reference_fg: np.ndarray | None = None,
                     match_cov: float = 0.90, warn_cov: float = 0.75, max_spill: float = 0.25):
    scale = min(8.0, 800.0 / max(plan.canvas_mm))
    size = (int(math.ceil(plan.canvas_mm[0] * scale)) + 2, int(math.ceil(plan.canvas_mm[1] * scale)) + 2)
    import cv2

    per, ref_union = [], np.zeros((size[1], size[0]), bool)
    for o in objects:
        reg = rasterize(o.geometry, o.holes_mm, scale, size) > 0
        ref_union |= reg
        if o.object_id not in plan.order:
            per.append(ObjectComparison(o.object_id, "MISSING", 0.0, 0.0, 0.0, 0.0, ["no stitches generated for this reference object"]))
            continue
        cov_m = _coverage_mask(plan, scale, size, o.object_id)
        area = max(int(reg.sum()), 1)
        near = cv2.dilate(reg.astype(np.uint8), np.ones((int(0.8 * scale) * 2 + 1,) * 2, np.uint8)) > 0
        cov = float((cov_m & reg).sum()) / area
        spill = float((cov_m & ~near).sum()) / area
        ys, xs = np.nonzero(cov_m)
        notes = []
        if len(xs):
            cx, cy = xs.mean() / scale, ys.mean() / scale
            ry, rx = np.nonzero(reg)
            shift = math.dist((cx, cy), (rx.mean() / scale, ry.mean() / scale))
            sw = (xs.max() - xs.min() + 1) / scale; sh = (ys.max() - ys.min() + 1) / scale
            rw = (rx.max() - rx.min() + 1) / scale; rh = (ry.max() - ry.min() + 1) / scale
            ratio = math.sqrt((sw * sh) / max(rw * rh, 1e-9))
        else:
            shift, ratio = 0.0, 0.0
        if cov >= match_cov and spill <= max_spill and shift < 1.5 and 0.85 <= ratio <= 1.15:
            st = "MATCH"
        elif cov < 0.3:
            st = "MISSING"; notes.append(f"only {cov*100:.0f}% of the region is covered")
        elif spill > max_spill * 2 or not (0.7 <= ratio <= 1.3) or shift > 3:
            st = "DISTORTED"; notes.append(f"spill {spill*100:.0f}%, size ratio {ratio:.2f}, centroid shift {shift:.1f}mm")
        elif cov >= warn_cov:
            st = "WARNING"; notes.append(f"coverage {cov*100:.0f}% below match threshold {match_cov*100:.0f}%")
        else:
            st = "WARNING"; notes.append(f"coverage {cov*100:.0f}%")
        oc = ObjectComparison(o.object_id, st, round(cov, 3), round(spill, 3), round(shift, 2), round(ratio, 3), notes)
        _contour_topology(oc, o, cov_m, scale)
        if not oc.topology_ok:
            oc.notes.append(f"topology differs: reference has {oc.holes_reference} hole(s), stitching leaves {oc.holes_stitched}")
            if oc.status == "MATCH":
                oc.status = "WARNING"
        elif oc.contour_max_mm is not None and oc.contour_max_mm > 2.5 and oc.status == "MATCH":
            oc.status = "WARNING"; oc.notes.append(f"contour deviates up to {oc.contour_max_mm:.1f}mm")
        per.append(oc)

    stitched = _coverage_mask(plan, scale, size)
    ref = ref_union
    if reference_fg is not None:
        ref = cv2.resize(reference_fg.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST) > 0
    tol = cv2.dilate(ref.astype(np.uint8), np.ones((int(0.8 * scale) * 2 + 1,) * 2, np.uint8)) > 0
    extra = float((stitched & ~tol).sum()) / max(int(ref.sum()), 1)
    inter = (stitched & ref).sum(); union = (stitched | ref).sum()
    global_report = {"coverage_of_reference": round(float((stitched & ref).sum()) / max(int(ref.sum()), 1), 3),
                     "stitched_outside_reference": round(extra, 3), "iou": round(float(inter) / max(int(union), 1), 3),
                     "extra_stitching": "EXTRA" if extra > 0.10 else "OK"}
    return per, global_report
