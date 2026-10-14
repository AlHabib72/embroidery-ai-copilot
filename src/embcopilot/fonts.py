"""
Font engine (v0.6): real vector glyph outlines from TTF/OTF/TTC via fontTools,
kerning (GPOS 'kern' feature or legacy 'kern' table), a font browser that scans
system + user folders, and an embroidery *suitability* analysis.

No font is bundled.  The engine loads whatever fonts the machine/user provides
(system fonts, ``~/.embcopilot/fonts``, ``EMBCOPILOT_FONT_DIRS``).  Commercial
embroidery font libraries are REQUIRES_LICENSED_RESOURCE: point the engine at
your licensed folder, it will not ship or fake them.

Coordinates: glyph space is y-UP, units = millimetres after ``scale_to_mm``.
"""
from __future__ import annotations

import logging
import math
import os
import sys
from dataclasses import dataclass, field

from .geometry import point_in_polygon, polygon_area

Point = tuple[float, float]

logging.getLogger("fontTools").setLevel(logging.ERROR)  # font timestamp noise

FONT_EXT = (".ttf", ".otf", ".ttc", ".otc")
_CACHE: dict = {}  # (path, mtime, ttc_index) -> TTFont ; glyph cache below
_GLYPH_CACHE: dict = {}


class FontError(Exception):
    pass


# --------------------------------------------------------------------------- discovery
def default_font_dirs() -> list[str]:
    dirs: list[str] = []
    env = os.environ.get("EMBCOPILOT_FONT_DIRS", "")
    dirs += [d for d in env.split(os.pathsep) if d]
    dirs.append(os.path.join(os.path.expanduser("~"), ".embcopilot", "fonts"))
    if sys.platform.startswith("win"):
        dirs.append(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    elif sys.platform == "darwin":
        dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                 os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if os.path.isdir(d)]


@dataclass
class FontInfo:
    path: str
    family: str
    style: str
    glyphs: int
    units_per_em: int
    has_kerning: bool

    def to_dict(self):
        return self.__dict__.copy()


def _open(path: str, index: int = 0):
    from fontTools.ttLib import TTFont
    try:
        key = (path, os.path.getmtime(path), index)
    except OSError as e:
        raise FontError(f"FONT_NOT_FOUND: {path}") from e
    f = _CACHE.get(key)
    if f is None:
        try:
            f = TTFont(path, fontNumber=index, lazy=True)
        except Exception as e:  # noqa: BLE001 - surface as friendly error
            raise FontError(f"FONT_UNREADABLE: {os.path.basename(path)} ({type(e).__name__})") from e
        if len(_CACHE) > 64:
            _CACHE.clear(); _GLYPH_CACHE.clear()
        _CACHE[key] = f
    return f


def font_info(path: str) -> FontInfo:
    f = _open(path)
    name = f["name"]
    fam = (name.getBestFamilyName() or os.path.basename(path)) if "name" in f else os.path.basename(path)
    sty = (name.getBestSubFamilyName() or "") if "name" in f else ""
    kern = "kern" in f or "GPOS" in f
    return FontInfo(path, fam, sty, len(f.getGlyphOrder()), f["head"].unitsPerEm, kern)


def scan_fonts(extra_dirs: list[str] | None = None, limit: int | None = None) -> list[FontInfo]:
    """Font browser data.  Unreadable files are skipped (never fatal)."""
    seen, out = set(), []
    for d in (extra_dirs or []) + default_font_dirs():
        for root, _, files in os.walk(d):
            for fn in sorted(files):
                if not fn.lower().endswith(FONT_EXT):
                    continue
                p = os.path.join(root, fn)
                rp = os.path.realpath(p)
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    out.append(font_info(p))
                except FontError:
                    continue
                if limit and len(out) >= limit:
                    return out
    return out


# --------------------------------------------------------------------------- outlines
@dataclass
class Shape:
    """One outer contour with its holes (counters).  y-up, mm after scaling."""
    outer: list[Point]
    holes: list[list[Point]] = field(default_factory=list)


@dataclass
class GlyphOutline:
    char: str
    shapes: list[Shape]
    advance: float          # horizontal advance
    xmin: float = 0.0
    xmax: float = 0.0
    missing: bool = False   # True when the font had no glyph for the character (never silently substituted)

    def contours(self):
        for s in self.shapes:
            yield s.outer
            yield from s.holes


def _bez_q(p0, p1, p2, n):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(1, n + 1))]


def _bez_c(p0, p1, p2, p3, n):
    out = []
    for i in range(1, n + 1):
        t = i / n; u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def _flatten_glyph(glyphset, name: str, curve_steps: int):
    from fontTools.pens.basePen import BasePen

    class Pen(BasePen):
        def __init__(self, gs):
            super().__init__(gs)
            self.contours: list[list[Point]] = []
            self.cur: list[Point] = []

        def _moveTo(self, p): self.cur = [p]
        def _lineTo(self, p): self.cur.append(p)
        def _curveToOne(self, a, b, c): self.cur += _bez_c(self._getCurrentPoint(), a, b, c, curve_steps)
        def _qCurveToOne(self, a, b): self.cur += _bez_q(self._getCurrentPoint(), a, b, curve_steps)
        def _closePath(self):
            if len(self.cur) > 2:
                if math.dist(self.cur[0], self.cur[-1]) < 1e-9:
                    self.cur.pop()
                self.contours.append(self.cur)
            self.cur = []
        _endPath = _closePath

    pen = Pen(glyphset)
    glyphset[name].draw(pen)
    return pen.contours


def group_contours(contours: list[list[Point]]) -> list[Shape]:
    """Even-odd nesting: depth 0 = outer, depth 1 = hole of its parent, depth 2 = island, ...
    This keeps O, A, B, P, R, D, Q as outer + counters (never a solid disk)."""
    cs = [c for c in contours if len(c) >= 3 and abs(polygon_area(c)) > 1e-12]
    depth, parent = [], []
    for i, c in enumerate(cs):
        probe = c[0]
        cont = [j for j, d in enumerate(cs) if j != i and abs(polygon_area(d)) > abs(polygon_area(c)) and point_in_polygon(probe, d)]
        depth.append(len(cont))
        parent.append(min(cont, key=lambda j: abs(polygon_area(cs[j]))) if cont else None)
    shapes: dict[int, Shape] = {}
    for i, c in enumerate(cs):
        if depth[i] % 2 == 0:
            shapes[i] = Shape(list(c), [])
    for i, c in enumerate(cs):
        if depth[i] % 2 == 1 and parent[i] in shapes:
            shapes[parent[i]].holes.append(list(c))
    return list(shapes.values())


def cap_height_units(font) -> float:
    upm = font["head"].unitsPerEm
    try:
        ch = getattr(font["OS/2"], "sCapHeight", 0)
        if ch and ch > 0:
            return float(ch)
    except Exception:  # noqa: BLE001
        pass
    gs = font.getGlyphSet(); cmap = font.getBestCmap() or {}
    if ord("H") in cmap:
        from fontTools.pens.boundsPen import BoundsPen
        bp = BoundsPen(gs); gs[cmap[ord("H")]].draw(bp)
        if bp.bounds:
            return float(bp.bounds[3])
    return 0.7 * upm


def glyph_outline(path: str, ch: str, cap_height_mm: float = 10.0, curve_steps: int = 10) -> GlyphOutline:
    """Glyph outline scaled so that the font's cap-height == ``cap_height_mm`` (y-up)."""
    key = (path, ch, round(cap_height_mm, 4), curve_steps)
    if key in _GLYPH_CACHE:
        return _GLYPH_CACHE[key]
    f = _open(path)
    cmap = f.getBestCmap() or {}
    gs = f.getGlyphSet()
    k = cap_height_mm / cap_height_units(f)
    name = cmap.get(ord(ch))
    if name is None:
        out = GlyphOutline(ch, [], 0.5 * f["head"].unitsPerEm * k, missing=True)
    else:
        conts = _flatten_glyph(gs, name, curve_steps)
        conts = [[(x * k, y * k) for x, y in c] for c in conts]
        shapes = group_contours(conts)
        xs = [p[0] for s in shapes for p in s.outer]
        out = GlyphOutline(ch, shapes, f["hmtx"][name][0] * k, min(xs) if xs else 0.0, max(xs) if xs else 0.0)
    _GLYPH_CACHE[key] = out
    return out


# --------------------------------------------------------------------------- kerning
def _kern_lookup_indices(f):
    gpos = f["GPOS"].table
    idx = set()
    for fr in gpos.FeatureList.FeatureRecord:
        if fr.FeatureTag == "kern":
            idx.update(fr.Feature.LookupListIndex)
    return sorted(idx), gpos


def kern_pair_units(path: str, left: str, right: str) -> float:
    """Kerning adjustment in font units (usually negative = tighter).  0 when the font has none."""
    f = _open(path)
    cmap = f.getBestCmap() or {}
    gl, gr = cmap.get(ord(left)), cmap.get(ord(right))
    if gl is None or gr is None:
        return 0.0
    if "GPOS" in f:
        try:
            idx, gpos = _kern_lookup_indices(f)
            for li in idx:
                lk = gpos.LookupList.Lookup[li]
                for st in lk.SubTable:
                    if lk.LookupType == 9:
                        st = st.ExtSubTable
                    if getattr(st, "LookupType", lk.LookupType) != 2 and lk.LookupType != 9:
                        continue
                    if not hasattr(st, "Coverage") or gl not in st.Coverage.glyphs:
                        continue
                    if st.Format == 1:
                        ps = st.PairSet[st.Coverage.glyphs.index(gl)]
                        for rec in ps.PairValueRecord:
                            if rec.SecondGlyph == gr and rec.Value1 is not None:
                                return float(getattr(rec.Value1, "XAdvance", 0) or 0)
                    elif st.Format == 2:
                        c1 = st.ClassDef1.classDefs.get(gl, 0)
                        c2 = st.ClassDef2.classDefs.get(gr, 0)
                        v = st.Class1Record[c1].Class2Record[c2].Value1
                        if v is not None and getattr(v, "XAdvance", 0):
                            return float(v.XAdvance)
        except Exception:  # noqa: BLE001 - malformed GPOS must not break layout
            pass
    if "kern" in f:
        for kt in f["kern"].kernTables:
            v = getattr(kt, "kernTable", {}).get((gl, gr))
            if v:
                return float(v)
    return 0.0


def kern_pair_mm(path: str, left: str, right: str, cap_height_mm: float) -> float:
    f = _open(path)
    return kern_pair_units(path, left, right) * cap_height_mm / cap_height_units(f)


# --------------------------------------------------------------------------- suitability
@dataclass
class GlyphSuitability:
    char: str
    status: str                 # SAFE | WARNING | UNSUITABLE
    min_stroke_mm: float | None
    min_counter_mm: float | None
    sharp_corners: int
    self_intersections: int
    messages: list[str] = field(default_factory=list)

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class SuitabilityThresholds:
    """Heuristic starting values -- NOT validated against sew-outs.  Tune per fabric/machine."""
    stroke_warn_mm: float = 0.9
    stroke_bad_mm: float = 0.5
    counter_warn_mm: float = 1.2
    counter_bad_mm: float = 0.6
    min_height_warn_mm: float = 5.0
    sharp_corner_deg: float = 35.0


def _segments_cross(a, b, c, d) -> bool:
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def _self_intersections(c: list[Point]) -> int:
    n, cnt = len(c), 0
    for i in range(n):
        a, b = c[i], c[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_cross(a, b, c[j], c[(j + 1) % n]):
                cnt += 1
    return cnt


def _sharp_corners(c: list[Point], deg: float) -> int:
    n, cnt = len(c), 0
    for i in range(n):
        p0, p1, p2 = c[i - 1], c[i], c[(i + 1) % n]
        v1 = (p0[0] - p1[0], p0[1] - p1[1]); v2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        cosv = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
        if math.degrees(math.acos(cosv)) < deg:
            cnt += 1
    return cnt


def analyse_glyph(g: GlyphOutline, th: SuitabilityThresholds | None = None, px_per_mm: float = 20.0) -> GlyphSuitability:
    import cv2
    import numpy as np
    th = th or SuitabilityThresholds()
    msgs: list[str] = []
    if g.missing:
        return GlyphSuitability(g.char, "UNSUITABLE", None, None, 0, 0, [f"GLYPH_MISSING: font has no glyph for {g.char!r} (never substituted silently)"])
    if not g.shapes:  # space etc.
        return GlyphSuitability(g.char, "SAFE", None, None, 0, 0, [])
    xs = [p[0] for c in g.contours() for p in c]; ys = [p[1] for c in g.contours() for p in c]
    x0, y0 = min(xs), min(ys)
    W = int((max(xs) - x0) * px_per_mm) + 6; H = int((max(ys) - y0) * px_per_mm) + 6
    tr = lambda c: np.array([[(x - x0) * px_per_mm + 3, (max(ys) - y) * px_per_mm + 3] for x, y in c], np.int32)  # noqa: E731
    mask = np.zeros((H, W), np.uint8)
    holes_masks = []
    for s in g.shapes:
        cv2.fillPoly(mask, [tr(s.outer)], 255)
    for s in g.shapes:
        for h in s.holes:
            cv2.fillPoly(mask, [tr(h)], 0)
            hm = np.zeros((H, W), np.uint8); cv2.fillPoly(hm, [tr(h)], 255); holes_masks.append(hm)
    dt = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    # ridge ~ local maxima of the distance transform (approximate medial axis)
    dil = cv2.dilate(dt, np.ones((3, 3), np.uint8))
    ridge = (dt >= dil - 1e-6) & (dt > 1.0)
    min_stroke = float(np.percentile(2 * dt[ridge], 5) / px_per_mm) if ridge.any() else None
    min_counter = None
    for hm in holes_masks:
        d = cv2.distanceTransform(hm, cv2.DIST_L2, 5)
        v = float(2 * d.max() / px_per_mm)
        min_counter = v if min_counter is None else min(min_counter, v)
    corners = sum(_sharp_corners(c, th.sharp_corner_deg) for c in g.contours())
    inter = sum(_self_intersections(c) for c in g.contours())
    status = "SAFE"
    if min_stroke is not None:
        if min_stroke < th.stroke_bad_mm:
            status = "UNSUITABLE"; msgs.append(f"stroke ~{min_stroke:.2f} mm < {th.stroke_bad_mm} mm: will not sew cleanly")
        elif min_stroke < th.stroke_warn_mm:
            status = "WARNING"; msgs.append(f"stroke ~{min_stroke:.2f} mm < {th.stroke_warn_mm} mm: thin; consider larger size or run-stitch lettering")
    if min_counter is not None:
        if min_counter < th.counter_bad_mm:
            status = "UNSUITABLE"; msgs.append(f"counter {min_counter:.2f} mm < {th.counter_bad_mm} mm: counter will close")
        elif min_counter < th.counter_warn_mm:
            if status == "SAFE": status = "WARNING"
            msgs.append(f"counter {min_counter:.2f} mm < recommended {th.counter_warn_mm} mm: may close after sew-out")
    if inter:
        if status == "SAFE": status = "WARNING"
        msgs.append(f"{inter} self-intersection(s) in outline")
    return GlyphSuitability(g.char, status, None if min_stroke is None else round(min_stroke, 3),
                            None if min_counter is None else round(min_counter, 3), corners, inter, msgs)


def analyse_text(path: str, text: str, cap_height_mm: float, th: SuitabilityThresholds | None = None) -> dict:
    th = th or SuitabilityThresholds()
    seen, res = set(), []
    for ch in text:
        if ch.isspace() or ch in seen:
            continue
        seen.add(ch)
        res.append(analyse_glyph(glyph_outline(path, ch, cap_height_mm), th))
    order = {"SAFE": 0, "WARNING": 1, "UNSUITABLE": 2}
    worst = max((r.status for r in res), key=lambda s: order[s], default="SAFE")
    notes = []
    if cap_height_mm < th.min_height_warn_mm:
        notes.append(f"SIZE_WARNING: cap height {cap_height_mm:g} mm < {th.min_height_warn_mm:g} mm (heuristic) -- small lettering needs a suitable font/density")
        if worst == "SAFE": worst = "WARNING"
    return {"status": worst, "glyphs": [r.to_dict() for r in res], "notes": notes,
            "disclaimer": "heuristic thresholds; real sew-out validation required"}
