"""Small, dependency-light geometry helpers (numpy only). All units mm."""

from __future__ import annotations

import math

import numpy as np

Point = tuple[float, float]


def polygon_area(poly: list[Point]) -> float:
    """Absolute shoelace area."""
    if len(poly) < 3:
        return 0.0
    a = np.asarray(poly, float)
    x, y = a[:, 0], a[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def polyline_length(pts: list[Point]) -> float:
    if len(pts) < 2:
        return 0.0
    a = np.asarray(pts, float)
    return float(np.hypot(*np.diff(a, axis=0).T).sum())


def resample_polyline(pts: list[Point], step: float) -> list[Point]:
    """Points every `step` mm along the polyline (always includes both ends)."""
    if len(pts) < 2 or step <= 0:
        return list(pts)
    a = np.asarray(pts, float)
    seg = np.hypot(*np.diff(a, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 1e-9:
        return [tuple(a[0])]
    n = max(1, int(math.ceil(total / step)))
    t = np.linspace(0, total, n + 1)
    x = np.interp(t, cum, a[:, 0])
    y = np.interp(t, cum, a[:, 1])
    return [(float(px), float(py)) for px, py in zip(x, y)]


def point_in_polygon(pt: Point, poly: list[Point]) -> bool:
    """Even-odd ray casting."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def point_in_region(pt: Point, outer: list[Point], holes: list[list[Point]]) -> bool:
    return point_in_polygon(pt, outer) and not any(point_in_polygon(pt, h) for h in holes)


def dist_point_to_polyline(pt: Point, poly: list[Point], closed: bool = True) -> float:
    a = np.asarray(poly, float)
    if closed:
        b = np.roll(a, -1, axis=0)
    else:
        a, b = a[:-1], a[1:]
    p = np.asarray(pt, float)
    ab = b - a
    denom = (ab * ab).sum(1)
    denom = np.where(denom < 1e-12, 1e-12, denom)
    t = np.clip(((p - a) * ab).sum(1) / denom, 0, 1)
    proj = a + ab * t[:, None]
    return float(np.hypot(*(proj - p).T).min())


def polylines_intersect(a: list[Point], b: list[Point] | None = None) -> bool:
    """True if polyline `a` crosses `b` (or itself when b is None; adjacent
    segments are ignored). Vectorised O(n*m)."""
    A = np.asarray(a, float)
    self_test = b is None
    B = A if self_test else np.asarray(b, float)
    if len(A) < 2 or len(B) < 2:
        return False
    p, r = A[:-1][:, None, :], (A[1:] - A[:-1])[:, None, :]
    q, s = B[:-1][None, :, :], (B[1:] - B[:-1])[None, :, :]
    rxs = r[..., 0] * s[..., 1] - r[..., 1] * s[..., 0]
    qp = q - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (qp[..., 0] * s[..., 1] - qp[..., 1] * s[..., 0]) / rxs
        u = (qp[..., 0] * r[..., 1] - qp[..., 1] * r[..., 0]) / rxs
    hit = (np.abs(rxs) > 1e-12) & (t > 1e-9) & (t < 1 - 1e-9) & (u > 1e-9) & (u < 1 - 1e-9)
    if self_test:
        n = len(A) - 1
        idx = np.arange(n)
        far = np.abs(idx[:, None] - idx[None, :]) > 1
        hit &= far
    return bool(hit.any())


def normals(pts: list[Point]) -> np.ndarray:
    """Unit left-hand normals at each vertex (central differences)."""
    a = np.asarray(pts, float)
    d = np.gradient(a, axis=0) if len(a) > 2 else np.diff(a, axis=0, prepend=a[:1])
    if len(a) == 2:
        d = np.repeat((a[1] - a[0])[None, :], 2, axis=0)
    n = np.stack([-d[:, 1], d[:, 0]], 1)
    ln = np.hypot(n[:, 0], n[:, 1])
    ln[ln < 1e-12] = 1.0
    return n / ln[:, None]


def smooth_polyline(pts: list[Point], window: int = 5) -> list[Point]:
    """Moving average that keeps the two end points fixed."""
    if len(pts) < 3 or window < 3:
        return list(pts)
    a = np.asarray(pts, float)
    k = window | 1
    pad = k // 2
    ap = np.pad(a, ((pad, pad), (0, 0)), mode="edge")
    ker = np.ones(k) / k
    sm = np.stack([np.convolve(ap[:, i], ker, mode="valid") for i in (0, 1)], 1)
    sm[0], sm[-1] = a[0], a[-1]
    return [(float(x), float(y)) for x, y in sm]


def bbox(pts: list[Point]) -> tuple[float, float, float, float]:
    a = np.asarray(pts, float)
    return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())


def rasterize(outer: list[Point], holes: list[list[Point]], scale: float, size_px: tuple[int, int]):
    """Filled mask (uint8 0/255) of outer minus holes at `scale` px per mm."""
    import cv2

    w, h = size_px
    m = np.zeros((h, w), np.uint8)
    f = lambda pts: np.round(np.asarray(pts, float) * scale).astype(np.int32)  # noqa: E731
    if len(outer) >= 3:
        cv2.fillPoly(m, [f(outer)], 255)
    for hp in holes:
        if len(hp) >= 3:
            cv2.fillPoly(m, [f(hp)], 0)
    return m
