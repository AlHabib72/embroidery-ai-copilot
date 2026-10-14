"""
Vector input (spec 13-16): SVG parsed directly (no rasterisation of the
geometry), and Adobe .ai files ONLY when they are PDF-compatible and contain
plain path operators. Both feed one canonical VectorDocument -> EmbroideryObjects.

Preserved: Bezier/arc curves (adaptively flattened), transforms, groups,
fill/stroke/opacity-free colours, compound paths + holes (even-odd nesting).
Not supported (reported, never silently degraded): CSS <style> sheets, clipPaths,
masks, gradients/patterns (fill becomes the first stop or is rejected), text
(convert to outlines first), embedded images, <use>.
A small internal raster is used ONLY to *measure* widths/skeleton; geometry
stays the exact vector outline.
"""

from __future__ import annotations

import math
import re
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from .domain import EmbroideryObject, new_id
from .geometry import polygon_area, resample_polyline

Pt = tuple[float, float]


class VectorError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code


@dataclass
class VectorPath:
    subpaths: list[list[Pt]]  # flattened, in mm
    closed: list[bool]
    fill: str | None  # "rgb(r,g,b)" or None
    stroke: str | None
    stroke_width_mm: float = 0.0
    fill_rule: str = "evenodd"
    group: str = ""


@dataclass
class VectorDocument:
    width_mm: float
    height_mm: float
    paths: list[VectorPath] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_kind: str = "svg"


# ------------------------------------------------------------ colours / units
_NAMED = {"black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0), "green": (0, 128, 0), "blue": (0, 0, 255),
          "yellow": (255, 255, 0), "orange": (255, 165, 0), "purple": (128, 0, 128), "gray": (128, 128, 128), "grey": (128, 128, 128),
          "pink": (255, 192, 203), "brown": (165, 42, 42), "navy": (0, 0, 128), "teal": (0, 128, 128), "gold": (255, 215, 0)}


def parse_color(v: str | None):
    if v is None:
        return None
    v = v.strip().lower()
    if v in ("none", "transparent", ""):
        return None
    if v.startswith("url("):
        raise VectorError("SVG_PAINT_SERVER_UNSUPPORTED", "gradients/patterns are not supported; use flat colours")
    if v in _NAMED:
        r, g, b = _NAMED[v]
    elif v.startswith("#"):
        h = v[1:]
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    elif v.startswith("rgb"):
        n = re.findall(r"[\d.]+%?", v)[:3]
        r, g, b = (int(round(float(x[:-1]) * 2.55)) if x.endswith("%") else int(float(x)) for x in n)
    else:
        raise VectorError("SVG_COLOR_UNSUPPORTED", f"colour {v!r}")
    return f"rgb({r},{g},{b})"


_UNIT = {"": 25.4 / 96, "px": 25.4 / 96, "mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6}


def parse_length(v: str | None, default=None):
    if v is None:
        return default
    m = re.match(r"\s*([-+]?[\d.]+(?:e[-+]?\d+)?)\s*([a-z%]*)", v.lower())
    if not m or m.group(2) == "%":
        return default
    return float(m.group(1)) * _UNIT.get(m.group(2), 1.0)


# ----------------------------------------------------------------- transforms
def _mat(a, b, c, d, e, f): return np.array([[a, c, e], [b, d, f], [0, 0, 1]], float)


def parse_transform(s: str | None) -> np.ndarray:
    M = np.eye(3)
    if not s:
        return M
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        n = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "matrix": T = _mat(*n)
        elif name == "translate": T = _mat(1, 0, 0, 1, n[0], n[1] if len(n) > 1 else 0)
        elif name == "scale": T = _mat(n[0], 0, 0, n[1] if len(n) > 1 else n[0], 0, 0)
        elif name == "rotate":
            a = math.radians(n[0]); c, s_ = math.cos(a), math.sin(a)
            T = _mat(c, s_, -s_, c, 0, 0)
            if len(n) == 3:
                T = _mat(1, 0, 0, 1, n[1], n[2]) @ T @ _mat(1, 0, 0, 1, -n[1], -n[2])
        elif name == "skewX": T = _mat(1, 0, math.tan(math.radians(n[0])), 1, 0, 0)
        elif name == "skewY": T = _mat(1, math.tan(math.radians(n[0])), 0, 1, 0, 0)
        else: raise VectorError("SVG_TRANSFORM_UNSUPPORTED", name)
        M = M @ T
    return M


# ------------------------------------------------------------------ geometry
def _bez(p0, p1, p2, p3, tol, out, depth=0):
    """Adaptive flattening: subdivide until control points are within tol of the chord."""
    def dist(p, a, b):
        ax, ay = b[0] - a[0], b[1] - a[1]
        L = math.hypot(ax, ay)
        if L < 1e-12:
            return math.hypot(p[0] - a[0], p[1] - a[1])
        return abs(ax * (a[1] - p[1]) - (a[0] - p[0]) * ay) / L
    if depth > 14 or max(dist(p1, p0, p3), dist(p2, p0, p3)) <= tol:
        out.append(p3); return
    m = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)  # noqa: E731
    p01, p12, p23 = m(p0, p1), m(p1, p2), m(p2, p3)
    p012, p123 = m(p01, p12), m(p12, p23)
    mid = m(p012, p123)
    _bez(p0, p01, p012, mid, tol, out, depth + 1)
    _bez(mid, p123, p23, p3, tol, out, depth + 1)


def _arc(p0, rx, ry, phi, fa, fs, p1, tol, out):
    if rx == 0 or ry == 0 or p0 == p1:
        out.append(p1); return
    rx, ry = abs(rx), abs(ry); ph = math.radians(phi); c, s = math.cos(ph), math.sin(ph)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = x1**2 / rx**2 + y1**2 / ry**2
    if lam > 1: rx *= math.sqrt(lam); ry *= math.sqrt(lam)
    num = rx**2 * ry**2 - rx**2 * y1**2 - ry**2 * x1**2
    den = rx**2 * y1**2 + ry**2 * x1**2
    co = math.sqrt(max(0, num / den)) * (-1 if fa == fs else 1)
    cxp, cyp = co * rx * y1 / ry, -co * ry * x1 / rx
    cx, cy = c * cxp - s * cyp + (p0[0] + p1[0]) / 2, s * cxp + c * cyp + (p0[1] + p1[1]) / 2
    ang = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)  # noqa: E731
    t1 = ang(1, 0, (x1 - cxp) / rx, (y1 - cyp) / ry)
    dt = ang((x1 - cxp) / rx, (y1 - cyp) / ry, (-x1 - cxp) / rx, (-y1 - cyp) / ry)
    if not fs and dt > 0: dt -= 2 * math.pi
    if fs and dt < 0: dt += 2 * math.pi
    r = max(rx, ry)
    n = max(4, int(abs(dt) / (2 * math.acos(max(0.0, 1 - tol / max(r, tol * 2)))) + 1))
    for k in range(1, n + 1):
        t = t1 + dt * k / n
        px, py = rx * math.cos(t), ry * math.sin(t)
        out.append((c * px - s * py + cx, s * px + c * py + cy))


_TOK = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")


def parse_path_d(d: str, tol: float = 0.02):
    """Returns [(points, closed)] in user units."""
    toks = [(m.group(1), m.group(2)) for m in _TOK.finditer(d)]
    i, subs, cur, start, cmd = 0, [], [], (0.0, 0.0), None
    pos = (0.0, 0.0); lastc = None; lastq = None
    def num():
        nonlocal i
        if i >= len(toks) or toks[i][1] is None:
            raise VectorError("SVG_PATH_SYNTAX", "expected number")
        v = float(toks[i][1]); i += 1; return v
    def flag():
        nonlocal i
        t = toks[i][1]; i += 1
        return int(float(t))
    while i < len(toks):
        if toks[i][0]:
            cmd = toks[i][0]; i += 1
            if cmd in "Zz":
                if cur: subs.append((cur, True)); cur = []
                pos = start; lastc = lastq = None; continue
        elif cmd is None:
            raise VectorError("SVG_PATH_SYNTAX", "path must start with a command")
        rel = cmd.islower(); C = cmd.upper()
        ox, oy = pos if rel else (0.0, 0.0)
        if C == "M":
            x, y = num() + ox, num() + oy
            if cur: subs.append((cur, False))
            cur = [(x, y)]; pos = start = (x, y); cmd = "l" if rel else "L"; lastc = lastq = None
        elif C == "L":
            x, y = num() + ox, num() + oy; cur.append((x, y)); pos = (x, y); lastc = lastq = None
        elif C == "H":
            x = num() + (pos[0] if rel else 0); pos = (x, pos[1]); cur.append(pos); lastc = lastq = None
        elif C == "V":
            y = num() + (pos[1] if rel else 0); pos = (pos[0], y); cur.append(pos); lastc = lastq = None
        elif C in "CS":
            if C == "C":
                p1 = (num() + ox, num() + oy)
            else:
                p1 = (2 * pos[0] - lastc[0], 2 * pos[1] - lastc[1]) if lastc else pos
            p2 = (num() + ox, num() + oy); p3 = (num() + ox, num() + oy)
            _bez(pos, p1, p2, p3, tol, cur); pos = p3; lastc = p2; lastq = None
        elif C in "QT":
            if C == "Q":
                q = (num() + ox, num() + oy)
            else:
                q = (2 * pos[0] - lastq[0], 2 * pos[1] - lastq[1]) if lastq else pos
            p3 = (num() + ox, num() + oy)
            c1 = (pos[0] + 2 / 3 * (q[0] - pos[0]), pos[1] + 2 / 3 * (q[1] - pos[1]))
            c2 = (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1]))
            _bez(pos, c1, c2, p3, tol, cur); pos = p3; lastq = q; lastc = None
        elif C == "A":
            rx, ry, phi = num(), num(), num(); fa, fs = flag(), flag(); p1 = (num() + ox, num() + oy)
            _arc(pos, rx, ry, phi, fa, fs, p1, tol, cur); pos = p1; lastc = lastq = None
        else:
            raise VectorError("SVG_PATH_SYNTAX", f"command {cmd}")
    if cur: subs.append((cur, False))
    return subs


# ----------------------------------------------------------------------- SVG
_SHAPES = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
_SKIP = {"defs", "clipPath", "mask", "style", "metadata", "title", "desc", "symbol", "pattern", "linearGradient", "radialGradient"}


def _style(el, inherited):
    st = dict(inherited)
    for k in ("fill", "stroke", "stroke-width", "fill-rule"):
        if el.get(k) is not None: st[k] = el.get(k)
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1); st[k.strip()] = v.strip()
    return st


def parse_svg(path_or_text: str, target_mm: tuple[float, float] | None = None, tol_mm: float = 0.05) -> VectorDocument:
    try:
        text = open(path_or_text, "rb").read() if "\n" not in path_or_text and "<" not in path_or_text else path_or_text.encode()
        root = ET.fromstring(text)
    except (ET.ParseError, OSError) as e:
        raise VectorError("SVG_CORRUPT", str(e))
    if not root.tag.endswith("svg"):
        raise VectorError("SVG_CORRUPT", "root element is not <svg>")
    ns = lambda t: t.split("}")[-1]  # noqa: E731
    vb = [float(x) for x in re.split(r"[\s,]+", root.get("viewBox", "").strip()) if x] if root.get("viewBox") else None
    w_mm = parse_length(root.get("width")); h_mm = parse_length(root.get("height"))
    if vb:
        if w_mm is None: w_mm = vb[2] * _UNIT["px"]
        if h_mm is None: h_mm = vb[3] * _UNIT["px"]
        base = _mat(w_mm / vb[2], 0, 0, h_mm / vb[3], -vb[0] * w_mm / vb[2], -vb[1] * h_mm / vb[3])
    else:
        if w_mm is None or h_mm is None:
            raise VectorError("SVG_NO_SIZE", "SVG needs a viewBox or width/height")
        base = _mat(_UNIT["px"], 0, 0, _UNIT["px"], 0, 0)
    doc = VectorDocument(w_mm, h_mm, source_kind="svg")
    for el in root.iter():
        if ns(el.tag) in ("style",) and (el.text or "").strip():
            doc.warnings.append("CSS <style> sheet ignored: only presentation attributes and inline style are supported")
        if ns(el.tag) in ("text", "image", "use"):
            doc.warnings.append(f"<{ns(el.tag)}> is not supported and was skipped (convert text to outlines first)")

    def walk(el, M, inh, gname):
        tag = ns(el.tag)
        if tag in _SKIP:
            return
        M2 = M @ parse_transform(el.get("transform"))
        st = _style(el, inh)
        if tag == "g" or el is root:
            for ch in el:
                walk(ch, M2, st, gname + "/" + (el.get("id") or "g") if tag == "g" else gname)
            return
        if tag not in _SHAPES:
            return
        subs = _shape_to_subpaths(el, tag, tol_mm / max(1e-9, math.sqrt(abs(np.linalg.det(M2[:2, :2])))))
        if not subs:
            return
        f = st.get("fill", "black")
        try:
            fill = parse_color(f); stroke = parse_color(st.get("stroke"))
        except VectorError as e:
            doc.warnings.append(str(e)); return
        # stroke width is in user units: scale by the transform
        sc = math.sqrt(abs(np.linalg.det(M2[:2, :2])))
        sw_mm = float(st.get("stroke-width", "1").replace("px", "")) * sc if stroke else 0.0
        out, closed = [], []
        for pts, cl in subs:
            P = np.array([(x, y, 1.0) for x, y in pts]) @ M2.T
            out.append([(float(a), float(b)) for a, b, _ in P]); closed.append(cl or tag in ("polygon", "rect", "circle", "ellipse"))
        doc.paths.append(VectorPath(out, closed, fill, stroke, sw_mm, st.get("fill-rule", "nonzero"), gname))

    walk(root, base, {}, "")
    if target_mm and doc.paths:
        pts = [p for vp in doc.paths for sp in vp.subpaths for p in sp]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        bw, bh = max(xs) - min(xs), max(ys) - min(ys)
        s = min(target_mm[0] / max(bw, 1e-9), target_mm[1] / max(bh, 1e-9))
        for vp in doc.paths:
            vp.subpaths = [[((x - min(xs)) * s, (y - min(ys)) * s) for x, y in sp] for sp in vp.subpaths]
            vp.stroke_width_mm *= s
        doc.width_mm, doc.height_mm = bw * s, bh * s
        doc.warnings.append(f"vector scaled x{s:.3f} to fit the {target_mm[0]:.0f}x{target_mm[1]:.0f}mm target")
    return doc


def _shape_to_subpaths(el, tag, tol):
    g = lambda k, d=0.0: float(re.sub(r"[a-z%]+$", "", el.get(k, str(d)))) if el.get(k) is not None else d  # noqa: E731
    if tag == "path":
        return parse_path_d(el.get("d", ""), tol)
    if tag == "rect":
        x, y, w, h = g("x"), g("y"), g("width"), g("height")
        return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)] if w > 0 and h > 0 else []
    if tag in ("circle", "ellipse"):
        cx, cy = g("cx"), g("cy"); rx = g("r") if tag == "circle" else g("rx"); ry = rx if tag == "circle" else g("ry")
        if rx <= 0 or ry <= 0: return []
        n = max(24, int(math.pi / math.acos(max(0.0, 1 - tol / max(rx, ry, tol * 2))))) if tol > 0 else 64
        return [([(cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n)) for k in range(n)], True)]
    if tag == "line":
        return [([(g("x1"), g("y1")), (g("x2"), g("y2"))], False)]
    if tag in ("polyline", "polygon"):
        n = [float(v) for v in re.split(r"[\s,]+", el.get("points", "").strip()) if v]
        return [(list(zip(n[0::2], n[1::2])), tag == "polygon")] if len(n) >= 4 else []
    return []


# ----------------------------------------------- vector -> embroidery objects
def _nest(subs):
    """Even-odd nesting: depth = number of other rings containing this ring's first point."""
    from .geometry import point_in_polygon
    depth = []
    for i, s in enumerate(subs):
        depth.append(sum(point_in_polygon(s[0], t) for j, t in enumerate(subs) if j != i and len(t) >= 3))
    groups = []
    for i, s in enumerate(subs):
        if depth[i] % 2 == 0:
            holes = [subs[j] for j in range(len(subs)) if depth[j] == depth[i] + 1 and point_in_polygon(subs[j][0], s)]
            groups.append((s, holes))
    return groups


def _measure(outer, holes, canvas):
    """Widths/skeleton from a private raster; returns dict of measurements."""
    import cv2
    from .vision import LocalVisionProvider
    from .geometry import rasterize
    scale = min(10.0, 700.0 / max(canvas, 1e-6))
    size = (int(math.ceil(canvas * scale)) + 4, int(math.ceil(canvas * scale)) + 4)
    m = rasterize(outer, holes, scale, size)
    lv = LocalVisionProvider(min_area_px=4, min_hole_area_px=2)
    o = lv._build_object(cv2, np, m, (0, 0, 0), None, 1 / scale, 1 / scale, 1 / scale, 0)
    return o


def objects_from_vector(doc: VectorDocument) -> list[EmbroideryObject]:
    objs: list[EmbroideryObject] = []
    canvas = max(doc.width_mm, doc.height_mm)
    k = 0
    for vp in doc.paths:
        if vp.fill:
            rings = [sp for sp, cl in zip(vp.subpaths, vp.closed) if cl and len(sp) >= 3 and polygon_area(sp) > 0.05]
            for outer, holes in _nest(rings):
                k += 1
                meas = _measure(outer, holes, canvas)
                xs = [p[0] for p in outer]; ys = [p[1] for p in outer]
                o = EmbroideryObject(object_id=new_id("obj"), name=f"Shape {k}", category="unknown", geometry=list(outer),
                                     bbox_mm=(min(xs), min(ys), max(xs), max(ys)), color_group=vp.fill,
                                     source="vector:svg_fill", holes_mm=[list(h) for h in holes],
                                     area_mm2=polygon_area(outer) - sum(polygon_area(h) for h in holes))
                if meas is not None:  # measurements only; exact vector outline is kept
                    o.width_stats_mm, o.skeleton_topology = meas.width_stats_mm, meas.skeleton_topology
                    o.centerline_mm, o.centerline_widths_mm = meas.centerline_mm, meas.centerline_widths_mm
                from .domain import Confidence
                o.confidence = {"detection": Confidence(0.97, "exact vector geometry (no segmentation involved)")}
                objs.append(o)
        if vp.stroke and vp.stroke_width_mm > 0:
            for sp, cl in zip(vp.subpaths, vp.closed):
                if len(sp) < 2: continue
                path = list(sp) + ([sp[0]] if cl and sp[0] != sp[-1] else [])
                cl_pts = resample_polyline(path, 0.5)
                if len(cl_pts) < 3: continue
                k += 1
                w = vp.stroke_width_mm
                a = np.asarray(cl_pts); d = np.gradient(a, axis=0); n = np.stack([-d[:, 1], d[:, 0]], 1); n /= np.maximum(np.hypot(*n.T), 1e-9)[:, None]
                outline = [tuple(map(float, p)) for p in np.vstack([a + n * w / 2, (a - n * w / 2)[::-1]])]
                xs = [p[0] for p in outline]; ys = [p[1] for p in outline]
                from .domain import Confidence
                o = EmbroideryObject(object_id=new_id("obj"), name=f"Stroke {k}", category="outline", geometry=outline,
                                     bbox_mm=(min(xs), min(ys), max(xs), max(ys)), color_group=vp.stroke, source="vector:svg_stroke",
                                     centerline_mm=[tuple(map(float, p)) for p in cl_pts], centerline_widths_mm=[w] * len(cl_pts),
                                     area_mm2=polygon_area(outline), width_stats_mm={"min": w, "p05": w, "median": w, "p95": w, "max": w,
                                     "centerline_length_mm": float(np.hypot(*np.diff(a, axis=0).T).sum())},
                                     skeleton_topology={"endpoints": 0 if cl else 2, "branch_points": 0, "closed_loop": False})
                o.confidence = {"detection": Confidence(0.97, "exact vector stroke")}
                objs.append(o)
    return objs


# ------------------------------------------------------------ AI (PDF-compat)
def parse_ai(path: str, target_mm=None) -> VectorDocument:
    """Adobe .ai saved with PDF compatibility is a PDF: read page content path operators.
    Anything else (no %PDF header, encrypted, object streams holding the page, no path ops) is
    AI_FILE_UNSUPPORTED_VARIANT. Never falls back to a screenshot."""
    data = open(path, "rb").read()
    if not data.startswith(b"%PDF"):
        raise VectorError("AI_FILE_UNSUPPORTED_VARIANT", "not PDF-compatible (legacy PostScript/AI); export SVG or PDF-compatible AI from your vector editor")
    if b"/Encrypt" in data[:4096] or b"/Encrypt" in data[-4096:]:
        raise VectorError("AI_FILE_UNSUPPORTED_VARIANT", "encrypted PDF content")
    streams = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        raw = m.group(1)
        try:
            # decompressobj tolerates a stripped/extra trailing EOL byte that a regex over binary data can eat
            out = zlib.decompressobj().decompress(raw)
            streams.append(out if out else raw)
        except zlib.error:
            streams.append(raw)
    mb = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", data)
    x0, y0, x1, y1 = (float(v) for v in mb.groups()) if mb else (0, 0, 612, 792)
    pt = 25.4 / 72
    doc = VectorDocument((x1 - x0) * pt, (y1 - y0) * pt, source_kind="ai-pdf")
    found = False
    for s in streams:
        try:
            txt = s.decode("latin-1")
        except Exception:
            continue
        if not re.search(r"\b(m|re)\b", txt) or not re.search(r"\b(f\*?|S|B\*?|b\*?)\b", txt):
            continue
        found |= _pdf_ops(txt, doc, x0, y1, pt)
    if not found:
        raise VectorError("AI_FILE_UNSUPPORTED_VARIANT", "no plain path operators found in page content (objects may be in compressed object streams, forms or images)")
    if target_mm and doc.paths:
        pts = [p for vp in doc.paths for sp in vp.subpaths for p in sp]
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        s_ = min(target_mm[0] / max(max(xs) - min(xs), 1e-9), target_mm[1] / max(max(ys) - min(ys), 1e-9))
        for vp in doc.paths:
            vp.subpaths = [[((x - min(xs)) * s_, (y - min(ys)) * s_) for x, y in sp] for sp in vp.subpaths]
            vp.stroke_width_mm *= s_
        doc.width_mm, doc.height_mm = (max(xs) - min(xs)) * s_, (max(ys) - min(ys)) * s_
    return doc


def _pdf_ops(txt, doc, x0, ytop, pt) -> bool:
    toks = re.findall(r"[-+]?\d*\.?\d+|[A-Za-z*]+'?|/\w+", txt)
    stack, gs, ctm = [], [], np.eye(3)
    fill, stroke, lw = "rgb(0,0,0)", "rgb(0,0,0)", 1.0
    subs, cur, closed_flags, args = [], [], [], []
    pos = (0.0, 0.0); made = False
    def T(x, y):
        v = ctm @ np.array([x, y, 1.0]); return ((v[0] - x0) * pt, (ytop - v[1]) * pt)
    def commit(f, s, evenodd):
        nonlocal subs, cur, closed_flags, made
        if cur: subs.append(cur); closed_flags.append(False)
        if subs:
            doc.paths.append(VectorPath([list(sp) for sp in subs], list(closed_flags), fill if f else None, stroke if s else None,
                                        lw * math.sqrt(abs(np.linalg.det(ctm[:2, :2]))) * pt if s else 0.0, "evenodd" if evenodd else "nonzero"))
            made = True
        subs, cur, closed_flags = [], [], []
    def rgb(a): return "rgb(%d,%d,%d)" % tuple(int(round(max(0, min(1, v)) * 255)) for v in a)
    for t in toks:
        if re.match(r"[-+]?\d*\.?\d+$", t):
            args.append(float(t)); continue
        op = t
        if op == "q": gs.append((ctm.copy(), fill, stroke, lw))
        elif op == "Q" and gs: ctm, fill, stroke, lw = gs.pop()
        elif op == "cm" and len(args) >= 6: ctm = ctm @ _mat(*args[-6:])
        elif op == "w" and args: lw = args[-1]
        elif op == "rg" and len(args) >= 3: fill = rgb(args[-3:])
        elif op == "RG" and len(args) >= 3: stroke = rgb(args[-3:])
        elif op == "g" and args: fill = rgb([args[-1]] * 3)
        elif op == "G" and args: stroke = rgb([args[-1]] * 3)
        elif op in ("k", "K") and len(args) >= 4:
            c, m_, y, k = args[-4:]; col = rgb([(1 - c) * (1 - k), (1 - m_) * (1 - k), (1 - y) * (1 - k)])  # naive CMYK->RGB
            if op == "k": fill = col
            else: stroke = col
        elif op == "m" and len(args) >= 2:
            if cur: subs.append(cur); closed_flags.append(False)
            cur = [T(*args[-2:])]; pos = tuple(args[-2:])
        elif op == "l" and len(args) >= 2: cur.append(T(*args[-2:])); pos = tuple(args[-2:])
        elif op == "c" and len(args) >= 6:
            a = args[-6:]; p0 = T(*pos); out = []
            _bez(p0, T(a[0], a[1]), T(a[2], a[3]), T(a[4], a[5]), 0.05, out); cur += out; pos = (a[4], a[5])
        elif op == "v" and len(args) >= 4:
            a = args[-4:]; p0 = T(*pos); out = []; _bez(p0, p0, T(a[0], a[1]), T(a[2], a[3]), 0.05, out); cur += out; pos = (a[2], a[3])
        elif op == "y" and len(args) >= 4:
            a = args[-4:]; p0 = T(*pos); out = []; _bez(p0, T(a[0], a[1]), T(a[2], a[3]), T(a[2], a[3]), 0.05, out); cur += out; pos = (a[2], a[3])
        elif op == "re" and len(args) >= 4:
            x, y, w, h = args[-4:]
            if cur: subs.append(cur); closed_flags.append(False)
            subs.append([T(x, y), T(x + w, y), T(x + w, y + h), T(x, y + h)]); closed_flags.append(True); cur = []
        elif op == "h":
            if cur: subs.append(cur); closed_flags.append(True); cur = []
        elif op in ("f", "F", "f*"): commit(True, False, op == "f*")
        elif op in ("S",): commit(False, True, False)
        elif op in ("s",): 
            if cur: subs.append(cur); closed_flags.append(True); cur = []
            commit(False, True, False)
        elif op in ("B", "B*", "b", "b*"): commit(True, True, op.endswith("*"))
        elif op == "n": subs, cur, closed_flags = [], [], []
        args = []
    return made


def objects_from_file(path: str, kind: str, target_mm, fit: bool = False):
    """Vector artwork keeps its NATIVE physical size unless fit=True (then it is scaled uniformly into target_mm)."""
    t = target_mm if fit else None
    doc = parse_svg(path, t) if kind == "svg" else parse_ai(path, t)
    if not fit and target_mm and (abs(doc.width_mm - target_mm[0]) > 1 or abs(doc.height_mm - target_mm[1]) > 1):
        doc.warnings.append(f"vector native size {doc.width_mm:.1f}x{doc.height_mm:.1f}mm kept (requested {target_mm[0]:.0f}x{target_mm[1]:.0f}mm; enable 'fit vector to size' to scale)")
    return doc, objects_from_vector(doc)
