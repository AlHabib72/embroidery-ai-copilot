"""CircularBadgePlanner (v0.6): outer boundary / border / text band / inner ring / centre emblem.

Works from a FOREGROUND MASK (background already excluded) so it never treats the background as an
object.  Every number is measured; every role carries a confidence; nothing is flattened into one polygon.
Heuristic, tested on synthetic badges only (no real artwork was available).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .circlefit import Circle, infer_circle


@dataclass
class BadgePart:
    role: str                      # outer_border | inner_ring | circular_text | center_emblem | negative_space
    confidence: float
    detail: dict = field(default_factory=dict)

    def to_dict(self):
        return {"role": self.role, "confidence": round(self.confidence, 3), **self.detail}


@dataclass
class BadgePlan:
    detected: bool
    center_px: tuple[float, float] | None
    parts: list[BadgePart]
    protected_center_radius_mm: float | None
    notes: list[str] = field(default_factory=list)
    mm_per_px: float = 1.0

    def to_dict(self):
        return {"detected": self.detected, "center_px": self.center_px, "parts": [p.to_dict() for p in self.parts],
                "protected_center_radius_mm": self.protected_center_radius_mm, "notes": self.notes,
                "order": [p.role for p in self.parts]}

    def roles(self):
        return [p.role for p in self.parts]


def _contour_pts(c):
    return [(float(p[0][0]), float(p[0][1])) for p in c]


class CircularBadgePlanner:
    def __init__(self, min_ring_px: float = 3.0, concentric_tol: float = 0.06, min_circle_r_px: float = 12.0):
        self.min_ring_px = min_ring_px
        self.tol = concentric_tol
        self.min_r = min_circle_r_px

    def analyze(self, mask: np.ndarray, mm_per_px: float = 1.0) -> BadgePlan:
        import cv2
        m = (np.asarray(mask) > 0).astype(np.uint8) * 255
        cnts, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        if hier is None or not cnts:
            return BadgePlan(False, None, [], None, ["NO_FOREGROUND"], mm_per_px)
        hier = hier[0]
        info = []
        for i, c in enumerate(cnts):
            if len(c) < 8 or cv2.contourArea(c) < 6:
                continue
            circ = infer_circle(_contour_pts(c), rel_tol=0.03)
            info.append({"i": i, "hole": hier[i][3] >= 0, "parent": hier[i][3], "circle": circ, "area": cv2.contourArea(c)})
        circles = [d for d in info if d["circle"] is not None and d["circle"].r >= self.min_r]
        if not circles:
            return BadgePlan(False, None, [], None, ["NO_CIRCULAR_BOUNDARY: no contour is convincingly a circle"], mm_per_px)
        outer = max((d for d in circles if not d["hole"]), key=lambda d: d["circle"].r, default=None)
        if outer is None:
            return BadgePlan(False, None, [], None, ["NO_OUTER_CIRCLE"], mm_per_px)
        C0 = outer["circle"]
        conc = [d for d in circles if math.hypot(d["circle"].cx - C0.cx, d["circle"].cy - C0.cy) <= self.tol * C0.r]
        conc.sort(key=lambda d: -d["circle"].r)
        cx, cy = C0.cx, C0.cy
        parts: list[BadgePart] = []

        # rings = foreground outer circle whose child hole is also a concentric circle
        rings = []
        for d in conc:
            if d["hole"]:
                continue
            kids = [k for k in conc if k["hole"] and k["parent"] == d["i"]]
            if kids:
                k = max(kids, key=lambda z: z["circle"].r)
                w = d["circle"].r - k["circle"].r
                if w >= self.min_ring_px:
                    rings.append((d["circle"], k["circle"], w))
            else:  # solid disc (no hole)
                rings.append((d["circle"], None, d["circle"].r))
        rings.sort(key=lambda t: -t[0].r)
        solid = [r for r in rings if r[1] is None]
        real_rings = [r for r in rings if r[1] is not None]
        for idx, (o, i_, w) in enumerate(real_rings):
            q = max(0.0, 1 - max(o.circularity_error, i_.circularity_error) * 20)
            parts.append(BadgePart("outer_border" if idx == 0 else "inner_ring", 0.6 + 0.4 * q,
                                   {"outer_radius_mm": round(o.r * mm_per_px, 3), "inner_radius_mm": round(i_.r * mm_per_px, 3),
                                    "width_mm": round(w * mm_per_px, 3), "geometry": "parametric_circle",
                                    "recommended_stitch": "input_c_satin" if w * mm_per_px <= 7 else "tatami_ring"}))
        if not real_rings:
            return BadgePlan(False, (cx, cy), parts, None, ["CIRCLE_FOUND_BUT_NO_RING: a solid disc is not a badge border"], mm_per_px)

        # the free band inside the outermost ring
        band_out = real_rings[0][1].r
        band_in = real_rings[1][0].r if len(real_rings) > 1 else 0.0
        # classify every other (non-ring) foreground contour by where it sits
        ring_ids = {d["i"] for d in conc}
        glyph_like = [d for d in info if not d["hole"] and d["i"] not in ring_ids and d["area"] >= 6]
        text, emblem = [], []
        for d in glyph_like:
            M = cv2.moments(cnts[d["i"]])
            if M["m00"] <= 0:
                continue
            gx, gy = M["m10"] / M["m00"], M["m01"] / M["m00"]
            r = math.hypot(gx - cx, gy - cy)
            if r >= band_out:
                continue  # outside the border: not part of the badge band
            (text if r > band_in > 0 else emblem).append((d, gx, gy, r))
        if len(real_rings) == 1:  # no inner ring: split by radius gap instead
            rs = sorted(t[3] for t in text + emblem)
            allg = text + emblem
            text, emblem = [], []
            for t in allg:
                (text if t[3] > 0.55 * band_out else emblem).append(t)
        if len(text) >= 3:
            rr = np.array([t[3] for t in text]); ang = np.degrees(np.arctan2([t[2] - cy for t in text], [t[1] - cx for t in text]))
            r_spread = float(rr.std() / max(rr.mean(), 1e-9))
            span = _angular_span(ang)
            if r_spread < 0.12 and span > 40:
                parts.append(BadgePart("circular_text", float(min(0.95, 0.65 + 0.3 * (1 - r_spread / 0.12))),
                                       {"glyph_count": len(text), "baseline_radius_mm": round(float(rr.mean()) * mm_per_px, 3),
                                        "span_deg": round(span, 1), "radial_spread": round(r_spread, 4),
                                        "recommended": "vector_glyph_lettering (CurvedTextEngine)", "ocr_needed": True}))
            else:
                emblem += text
        else:
            emblem += text
        holes_in_emblem = 0
        if emblem:
            xs, ys = [], []
            for d, *_ in emblem:
                x, y, w, h = cv2.boundingRect(cnts[d["i"]]); xs += [x, x + w]; ys += [y, y + h]
            holes_in_emblem = sum(1 for d in info if d["hole"] and d["parent"] in {e[0]["i"] for e in emblem})
            parts.append(BadgePart("center_emblem", 0.7, {"contour_count": len(emblem), "negative_space_count": holes_in_emblem,
                                                       "bbox_mm": [round(min(xs) * mm_per_px, 2), round(min(ys) * mm_per_px, 2),
                                                                   round(max(xs) * mm_per_px, 2), round(max(ys) * mm_per_px, 2)]}))
        total_holes = sum(1 for d in info if d["hole"] and d["i"] not in ring_ids)
        if total_holes:
            parts.append(BadgePart("negative_space", 0.8, {"count": total_holes, "note": "glyph counters / emblem holes - never stitch through"}))
        # protected empty centre: free radius inside the emblem (if emblem exists) is NOT empty; only flag when no emblem
        prot = None
        notes = ["heuristic planner; verify roles in the approval gate"]
        if not emblem:
            prot = round((real_rings[-1][1].r if real_rings else band_out) * mm_per_px, 3)
            notes.append("centre is empty: protected travel zone proposed")
        return BadgePlan(True, (cx, cy), parts, prot, notes, mm_per_px)


def _angular_span(angles_deg) -> float:
    a = np.sort(np.asarray(angles_deg) % 360.0)
    gaps = np.diff(np.r_[a, a[0] + 360.0])
    return 360.0 - float(gaps.max())
