"""Input C / Column C engine v2 (v0.6): centerline -> left/right rails -> satin stitches.

For every sampled centerline point: tangent -> normal -> perpendicular stitch angle.  Normal angles are
UNWRAPPED and low-pass smoothed, so the 0/180/0/180 oscillation cannot occur; sharp turns are limited by
a curvature clamp on the inner rail (reported as UNSAFE_TURN) instead of folding the rail over itself.
Width may be constant, a list (resampled) or a function of t in [0,1] (variable width).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

Point = tuple[float, float]
STANDARD_WIDTHS_MM = (1, 2, 3, 4, 5, 6, 8)


@dataclass
class SatinResult:
    centerline: list[Point]
    left_rail: list[Point]
    right_rail: list[Point]
    stitches: list[Point]
    stitch_angles_deg: list[float]       # direction of every stitch segment (mod 180, display only)
    normal_angles_deg: list[float]       # unwrapped, smoothed normal angle per sample
    widths_mm: list[float]
    closed: bool
    split: dict
    warnings: list[str] = field(default_factory=list)
    oscillations: int = 0

    def to_dict(self):
        return {"closed": self.closed, "samples": len(self.centerline), "stitches": len(self.stitches),
                "width_min_mm": round(min(self.widths_mm), 3), "width_max_mm": round(max(self.widths_mm), 3),
                "split": self.split, "oscillations": self.oscillations, "warnings": self.warnings}


def satin_split_strategy(width_mm: float, safe_width_mm: float = 7.0) -> dict:
    """Never force an extremely wide satin because the user asked for it: report a strategy instead."""
    if width_mm <= safe_width_mm:
        return {"strategy": "single_column", "columns": 1, "column_width_mm": width_mm, "warning": None}
    n = int(math.ceil(width_mm / safe_width_mm))
    if n <= 3:
        return {"strategy": "split_satin" if n == 2 else "three_column_satin", "columns": n,
                "column_width_mm": round(width_mm / n, 3),
                "warning": f"SATIN_TOO_WIDE: {width_mm:g} mm > {safe_width_mm:g} mm; split into {n} columns (snag/loop risk if forced)"}
    return {"strategy": "tatami_conversion", "columns": 1, "column_width_mm": width_mm,
            "warning": f"SATIN_TOO_WIDE: {width_mm:g} mm; satin is unsafe, convert to tatami (needs approval)"}


def _resample(pts: np.ndarray, step: float, closed: bool) -> np.ndarray:
    P = np.vstack([pts, pts[:1]]) if closed else pts
    seg = np.hypot(*np.diff(P, axis=0).T)
    s = np.r_[0, np.cumsum(seg)]
    total = s[-1]
    n = max(2, int(round(total / step)) + (0 if closed else 1))
    t = np.linspace(0, total, n, endpoint=not closed)
    return np.c_[np.interp(t, s, P[:, 0]), np.interp(t, s, P[:, 1])]


def _smooth(a: np.ndarray, w: int, closed: bool) -> np.ndarray:
    if w <= 1 or len(a) < 3:
        return a
    w = min(w | 1, (len(a) // 2) * 2 - 1) if len(a) > 3 else 1
    if w <= 1:
        return a
    k = np.ones(w) / w
    pad = w // 2
    ext = np.r_[a[-pad:], a, a[:pad]] if closed else np.r_[np.full(pad, a[0]), a, np.full(pad, a[-1])]
    return np.convolve(ext, k, mode="valid")


def _width_samples(width, n: int) -> np.ndarray:
    if callable(width):
        return np.array([float(width(i / max(n - 1, 1))) for i in range(n)])
    if isinstance(width, (list, tuple, np.ndarray)):
        w = np.asarray(width, float)
        return np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(w)), w)
    return np.full(n, float(width))


def angle_oscillations(angles_deg: Sequence[float], threshold_deg: float = 90.0) -> int:
    """Count jumps between consecutive angles that exceed threshold (mod-180 aware) -> 0/180 flips."""
    a = np.asarray(angles_deg, float)
    if len(a) < 2:
        return 0
    d = np.abs(np.diff(a))
    return int(np.sum(d > threshold_deg))


def satin_from_centerline(path: Sequence[Point], width, density_mm: float = 0.4, closed: bool = False,
                          pull_comp_mm: float = 0.0, smooth_window: int = 5, safe_width_mm: float = 7.0,
                          min_width_mm: float = 0.8, max_turn_deg_per_stitch: float = 25.0) -> SatinResult:
    pts = np.asarray(path, float)
    if len(pts) < 2:
        raise ValueError("centerline needs >= 2 points")
    warn: list[str] = []
    C = _resample(pts, max(density_mm, 0.05), closed)
    n = len(C)
    # tangents (central differences) -> smoothed unit tangents
    if closed:
        T = np.roll(C, -1, axis=0) - np.roll(C, 1, axis=0)
    else:
        T = np.gradient(C, axis=0)
    Tn = T / (np.hypot(T[:, 0], T[:, 1])[:, None] + 1e-12)
    # smooth the UNIT VECTORS (periodic-safe), then unwrap: smoothing an unwrapped angle of a closed loop breaks at the seam
    Ts = np.c_[_smooth(Tn[:, 0], smooth_window, closed), _smooth(Tn[:, 1], smooth_window, closed)]
    ang_t = np.unwrap(np.arctan2(Ts[:, 1], Ts[:, 0]))
    # curvature-limited rate of change for the stitch angle (prevents instantaneous flips)
    maxd = math.radians(max_turn_deg_per_stitch)
    lim = ang_t.copy()
    for i in range(1, n):
        lim[i] = lim[i - 1] + max(-maxd, min(maxd, ang_t[i] - lim[i - 1]))
    if closed:  # a closed loop turns by +-2*pi in total; do not let the limiter lag across the seam
        lim = ang_t
    # the limited angle may lag: this is intentional at sharp corners and is reported
    lagged = float(np.max(np.abs(lim - ang_t))) if n else 0.0
    if lagged > math.radians(10):
        warn.append(f"UNSAFE_TURN: stitch angle rate-limited at a sharp turn (lag {math.degrees(lagged):.0f} deg); check corner")
    ang_n = lim + math.pi / 2.0              # normal = tangent rotated +90 deg
    N = np.c_[np.cos(ang_n), np.sin(ang_n)]
    W = _width_samples(width, n) + pull_comp_mm
    W = np.maximum(W, 0.0)
    if (W < min_width_mm).any():
        warn.append(f"SATIN_TOO_NARROW: min width {W.min():.2f} mm < {min_width_mm} mm; use run/triple-run")
    split = satin_split_strategy(float(W.max()), safe_width_mm)
    if split["warning"]:
        warn.append(split["warning"])
    # curvature clamp on the inside rail
    kappa = np.gradient(ang_t) / max(density_mm, 1e-6)
    hl = W / 2.0; hr = W / 2.0
    for i in range(n):
        k = kappa[i]
        if abs(k) * (W[i] / 2.0) > 0.9:
            lim_h = 0.9 / abs(k)
            if k > 0:   # turning left (ccw): left side is the inside
                hl[i] = min(hl[i], lim_h)
            else:
                hr[i] = min(hr[i], lim_h)
            warn.append("UNSAFE_TURN: inner rail clamped (radius of curvature < half width)") if "UNSAFE_TURN: inner" not in " ".join(warn) else None
    L = C + N * hl[:, None]
    R = C - N * hr[:, None]
    stitches = [tuple(map(float, (L[i] if i % 2 == 0 else R[i]))) for i in range(n)]
    ang = []
    for i in range(1, len(stitches)):
        dx, dy = stitches[i][0] - stitches[i - 1][0], stitches[i][1] - stitches[i - 1][1]
        ang.append(math.degrees(math.atan2(dy, dx)) % 180.0)
    # oscillation is measured on the unwrapped normal (what the user sees as stitch-line direction)
    osc = angle_oscillations(np.degrees(ang_n))
    return SatinResult([tuple(p) for p in C.tolist()], [tuple(p) for p in L.tolist()], [tuple(p) for p in R.tolist()],
                       stitches, ang, np.degrees(ang_n).tolist(), W.tolist(), closed, split, warn, osc)


def satin_ring(cx: float, cy: float, radius_mm: float, width_mm: float, density_mm: float = 0.4, **kw) -> SatinResult:
    """Parametric satin ring: centerline is a true circle (no raster noise)."""
    n = max(48, int(2 * math.pi * radius_mm / max(density_mm, 0.05)))
    pts = [(cx + radius_mm * math.cos(2 * math.pi * i / n), cy + radius_mm * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return satin_from_centerline(pts, width_mm, density_mm, closed=True, **kw)
