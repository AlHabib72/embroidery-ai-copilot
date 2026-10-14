"""Parametric circle inference (v0.6).

A perfect circle must be stored as a circle, not as a noisy raster contour.  ``fit_circle`` does an
algebraic (Kasa) fit followed by Gauss-Newton geometric refinement; ``infer_circle`` only returns a
circle when residual AND angular coverage say the contour really is one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

Point = tuple[float, float]


@dataclass
class Circle:
    cx: float
    cy: float
    r: float
    rms: float            # RMS radial residual
    max_dev: float        # worst radial deviation
    coverage_deg: float   # angular extent of the sample points (360 = closed loop)

    @property
    def circularity_error(self) -> float:
        return self.rms / self.r if self.r > 0 else 1.0

    def to_dict(self):
        return {k: round(v, 4) for k, v in self.__dict__.items()}


def fit_circle(points: list[Point], refine_iters: int = 12) -> Circle:
    P = np.asarray(points, float)
    if len(P) < 3:
        raise ValueError("need >= 3 points")
    x, y = P[:, 0], P[:, 1]
    A = np.c_[2 * x, 2 * y, np.ones(len(P))]
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0], sol[1]
    r = math.sqrt(max(sol[2] + cx * cx + cy * cy, 1e-12))
    for _ in range(refine_iters):  # geometric refinement
        dx, dy = x - cx, y - cy
        d = np.hypot(dx, dy) + 1e-12
        J = np.c_[-dx / d, -dy / d, -np.ones(len(P))]
        res = d - r
        step, *_ = np.linalg.lstsq(J, -res, rcond=None)
        cx, cy, r = cx + step[0], cy + step[1], r + step[2]
        if np.abs(step).max() < 1e-9:
            break
    d = np.hypot(x - cx, y - cy)
    res = d - r
    ang = np.sort(np.degrees(np.arctan2(y - cy, x - cx)) % 360.0)
    gaps = np.diff(np.r_[ang, ang[0] + 360.0])
    coverage = 360.0 - float(gaps.max())
    return Circle(float(cx), float(cy), float(r), float(np.sqrt(np.mean(res ** 2))), float(np.abs(res).max()), coverage)


def infer_circle(points: list[Point], rel_tol: float = 0.02, min_coverage_deg: float = 300.0) -> Circle | None:
    """Return a Circle only if the contour is convincingly circular, else None (keep the raw contour)."""
    if len(points) < 8:
        return None
    try:
        c = fit_circle(points)
    except (ValueError, np.linalg.LinAlgError):
        return None
    if c.r <= 0 or c.circularity_error > rel_tol or c.coverage_deg < min_coverage_deg:
        return None
    return c


def circle_polygon(c: Circle, chord_tol_mm: float = 0.03, min_segments: int = 24) -> list[Point]:
    """Polygon whose chord error is <= chord_tol_mm (sagitta)."""
    if c.r <= chord_tol_mm:
        return [(c.cx, c.cy)] * 3
    n = max(min_segments, int(math.ceil(math.pi / math.acos(max(-1.0, min(1.0, 1 - chord_tol_mm / c.r))))))
    return [(c.cx + c.r * math.cos(2 * math.pi * i / n), c.cy + c.r * math.sin(2 * math.pi * i / n)) for i in range(n)]
