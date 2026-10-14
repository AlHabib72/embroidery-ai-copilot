"""
CurvedTextEngine (v0.6) -- true text-on-circle layout from VECTOR glyph outlines.

Pipeline:  text -> glyph outlines (fonts.py) -> kerning -> arc-length layout ->
tangent direction -> per-glyph transform -> shapes (outer + counters preserved).

It never rotates rectangular blocks: every glyph is its real outline, positioned
by its advance width measured along the baseline circle (arc length), so spacing
stays correct around curves.  ``FOLLOW_BASELINE`` additionally *bends* each
outline along the baseline (type-on-path), the others place rigid glyphs.

Coordinates:  centre (cx, cy) is in canvas space (y DOWN, like the stitch plan).
Angles are visual degrees, counter-clockwise from east on screen (top = 90 deg).

Orientation modes
  RADIAL            letters read outward along radii (spokes); up = counter-clockwise tangent
  TANGENTIAL        rigid glyphs, up = outward, reading direction from ``direction``
  OUTWARD_READABLE  up = outward, reads clockwise (top-of-badge text)
  INWARD_READABLE   up = toward centre, reads counter-clockwise (bottom-of-badge text)
  FOLLOW_BASELINE   outlines bent along the baseline (outward, clockwise); ``inward=True`` for the bottom
  BOTTOM_AUTO_FLIP  chooses OUTWARD or INWARD for the whole string from its centre angle,
                    so bottom text is never upside-down
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .fonts import FontError, GlyphOutline, Shape, cap_height_units, glyph_outline, kern_pair_mm, _open

Point = tuple[float, float]


class TextMode(str, Enum):
    RADIAL = "RADIAL"
    TANGENTIAL = "TANGENTIAL"
    OUTWARD_READABLE = "OUTWARD_READABLE"
    INWARD_READABLE = "INWARD_READABLE"
    FOLLOW_BASELINE = "FOLLOW_BASELINE"
    BOTTOM_AUTO_FLIP = "BOTTOM_AUTO_FLIP"


@dataclass
class GlyphPlacement:
    char: str
    shapes: list[Shape]            # canvas coordinates (y down), counters preserved
    center_angle_deg: float        # visual angle of the glyph's arc-centre
    rotation_deg: float            # rigid rotation applied (0 for FOLLOW_BASELINE: bent)
    arc_start_mm: float            # arc length along the baseline where the glyph starts
    advance_mm: float
    index: int

    def bbox(self):
        pts = [p for s in self.shapes for p in s.outer]
        if not pts:
            return (0, 0, 0, 0)
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class CurvedText:
    text: str
    mode: str
    placements: list[GlyphPlacement]
    radius_mm: float
    center: Point
    start_angle_deg: float
    end_angle_deg: float
    span_deg: float
    clockwise: bool
    inward: bool
    cap_height_mm: float
    upright_fraction: float        # share of glyphs whose "up" points to the top half of the screen
    inner_radius_mm: float
    outer_radius_mm: float
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"text": self.text, "mode": self.mode, "radius_mm": self.radius_mm, "center": self.center,
                "start_angle_deg": round(self.start_angle_deg, 3), "end_angle_deg": round(self.end_angle_deg, 3),
                "span_deg": round(self.span_deg, 3), "clockwise": self.clockwise, "inward": self.inward,
                "cap_height_mm": self.cap_height_mm, "upright_fraction": round(self.upright_fraction, 3),
                "inner_radius_mm": round(self.inner_radius_mm, 3), "outer_radius_mm": round(self.outer_radius_mm, 3),
                "glyphs": [{"char": g.char, "center_angle_deg": round(g.center_angle_deg, 3),
                            "rotation_deg": round(g.rotation_deg, 3), "arc_start_mm": round(g.arc_start_mm, 3),
                            "advance_mm": round(g.advance_mm, 3), "counters": sum(len(s.holes) for s in g.shapes)}
                           for g in self.placements],
                "warnings": self.warnings}


def _measure(font_path: str, text: str, cap_h: float, spacing: float, kerning: bool, space_mm: float | None):
    """Return glyphs, per-glyph arc start (relative, mm) and total length (mm)."""
    glyphs = [glyph_outline(font_path, ch, cap_h) for ch in text]
    starts, s = [], 0.0
    for i, g in enumerate(glyphs):
        starts.append(s)
        adv = g.advance if not (text[i] == " " and space_mm is not None) else space_mm
        s += adv
        if i + 1 < len(text):
            s += spacing + (kern_pair_mm(font_path, text[i], text[i + 1], cap_h) if kerning else 0.0)
    return glyphs, starts, s


def layout_text_on_circle(
    text: str,
    font_path: str,
    radius_mm: float,
    cap_height_mm: float = 4.0,
    center: Point = (0.0, 0.0),
    center_angle_deg: float = 90.0,
    mode: str | TextMode = TextMode.BOTTOM_AUTO_FLIP,
    direction: str = "cw",
    letter_spacing_mm: float = 0.3,
    kerning: bool = True,
    start_angle_deg: float | None = None,
    end_angle_deg: float | None = None,
    space_width_mm: float | None = None,
    inward: bool | None = None,
    max_span_deg: float = 360.0,
    optical_centering: bool = True,
) -> CurvedText:
    """Lay ``text`` on a circle of baseline radius ``radius_mm``.

    * ``center_angle_deg`` - where the middle of the string sits (ignored if start/end given).
    * ``start_angle_deg``/``end_angle_deg`` - justify the string into that sector; letter spacing is
      solved from the arc length (kerning kept), so spacing is arc-length based.
    * OUTWARD modes put the glyph body OUTSIDE the baseline; INWARD modes put it INSIDE.
    """
    if radius_mm <= 0:
        raise ValueError("radius_mm must be > 0")
    if not text:
        raise ValueError("text is empty")
    mode = TextMode(mode)
    warnings: list[str] = []
    f = _open(font_path)  # raises FontError with a friendly code
    cap_h = cap_height_mm

    # ---- resolve effective rigid/bend orientation for the whole string
    cw = direction.lower() in ("cw", "clockwise")
    if mode == TextMode.BOTTOM_AUTO_FLIP:
        ca = center_angle_deg if start_angle_deg is None else (start_angle_deg + end_angle_deg) / 2.0 if end_angle_deg is not None else start_angle_deg
        bottom = math.sin(math.radians(ca)) < -1e-9
        eff = TextMode.INWARD_READABLE if bottom else TextMode.OUTWARD_READABLE
    else:
        eff = mode
    if eff == TextMode.OUTWARD_READABLE:
        up_out, cw = True, True
    elif eff == TextMode.INWARD_READABLE:
        up_out, cw = False, False
    elif eff == TextMode.TANGENTIAL:
        up_out = True if inward is None else not inward
    elif eff == TextMode.FOLLOW_BASELINE:
        up_out = True if inward is None else not inward
        cw = up_out  # outward bends read clockwise, inward bends counter-clockwise (keeps text readable)
    else:  # RADIAL
        up_out = True
    sgn = -1.0 if cw else 1.0  # angle change per unit arc

    # ---- spacing: natural, or solved to fill a sector
    spacing = letter_spacing_mm
    glyphs, starts, total = _measure(font_path, text, cap_h, spacing, kerning, space_width_mm)
    if start_angle_deg is not None and end_angle_deg is not None:
        sector = abs(((end_angle_deg - start_angle_deg + 180) % 360) - 180) if abs(end_angle_deg - start_angle_deg) < 359.999 else 360.0
        if abs(end_angle_deg - start_angle_deg) >= 180:
            sector = abs(end_angle_deg - start_angle_deg)
        want = math.radians(sector) * radius_mm
        natural_no_spacing = total - spacing * (len(text) - 1)
        if len(text) > 1:
            spacing = (want - natural_no_spacing) / (len(text) - 1)
            if spacing < 0:
                warnings.append(f"TEXT_TOO_LONG_FOR_SECTOR: needs negative letter spacing ({spacing:.2f} mm); reduce size or widen sector")
            glyphs, starts, total = _measure(font_path, text, cap_h, spacing, kerning, space_width_mm)
        theta0 = start_angle_deg
        arc_offset = 0.0
    else:
        span_c = total / radius_mm
        if optical_centering:
            # trailing side-bearing of the last glyph / leading of the first is not "ink": centre on ink extents
            lead = glyphs[0].xmin if glyphs[0].shapes else 0.0
            last = glyphs[-1]
            trail = (last.advance - last.xmax) if last.shapes else 0.0
            ink_total = total - lead - trail
            arc_offset = lead + ink_total / 2.0  # arc position that must sit at the centre angle
        else:
            arc_offset = total / 2.0
        theta0 = center_angle_deg - sgn * math.degrees(arc_offset / radius_mm)  # angle at arc position 0
        theta0 = theta0  # (sgn=-1 for cw: start is at larger angle)
    span_deg = math.degrees(total / radius_mm)
    if span_deg > max_span_deg:
        warnings.append(f"TEXT_SPAN_EXCEEDS_LIMIT: {span_deg:.1f} deg > {max_span_deg:.1f} deg")
    if span_deg > 360:
        warnings.append("TEXT_OVERLAPS_ITSELF: string longer than the circle")

    cx, cy = center
    placements: list[GlyphPlacement] = []
    up_flags = 0
    inner_r, outer_r = 1e18, 0.0
    for i, (g, s0) in enumerate(zip(glyphs, starts)):
        if g.missing:
            warnings.append(f"GLYPH_MISSING: {g.char!r} not in font")
        adv = g.advance if not (text[i] == " " and space_width_mm is not None) else space_width_mm
        s_mid = s0 + adv / 2.0
        th_mid = theta0 + sgn * math.degrees(s_mid / radius_mm)
        tr = math.radians(th_mid)
        rx, ry = math.cos(tr), math.sin(tr)            # outward unit vector (math coords, y up)
        t_ccw = (-ry, rx)                              # counter-clockwise tangent
        shapes_out: list[Shape] = []
        rot = 0.0

        def to_canvas(px, py):
            return (cx + px, cy - py)

        if eff == TextMode.FOLLOW_BASELINE:
            def bend(x, y, _s0=s0):
                s = _s0 + x
                th = math.radians(theta0 + sgn * math.degrees(s / radius_mm))
                r = radius_mm + (y if up_out else -y)
                return to_canvas(r * math.cos(th), r * math.sin(th))
            for sh in g.shapes:
                shapes_out.append(Shape([bend(*p) for p in sh.outer], [[bend(*p) for p in h] for h in sh.holes]))
            up_vec = (rx, ry) if up_out else (-rx, -ry)
        else:
            if eff == TextMode.RADIAL:
                ex, ey = rx, ry; ux, uy = t_ccw           # x axis outward, up = ccw tangent
            elif up_out:
                ux, uy = rx, ry
                ex, ey = (ry, -rx) if cw else (-ry, rx)   # reading direction
            else:
                ux, uy = -rx, -ry
                ex, ey = (ry, -rx) if cw else (-ry, rx)
            bx, by = radius_mm * rx, radius_mm * ry       # baseline point at glyph centre
            half = adv / 2.0
            def place(p, _bx=bx, _by=by, _ex=ex, _ey=ey, _ux=ux, _uy=uy, _half=half):
                lx, ly = p[0] - _half, p[1]
                return to_canvas(_bx + lx * _ex + ly * _ux, _by + lx * _ey + ly * _uy)
            for sh in g.shapes:
                shapes_out.append(Shape([place(p) for p in sh.outer], [[place(p) for p in h] for h in sh.holes]))
            up_vec = (ux, uy)
            rot = math.degrees(math.atan2(uy, ux)) - 90.0
        if up_vec[1] > -1e-9:
            up_flags += 1
        for sh in shapes_out:
            for (x, y) in sh.outer:
                d = math.hypot(x - cx, y - cy)
                inner_r, outer_r = min(inner_r, d), max(outer_r, d)
        placements.append(GlyphPlacement(g.char, shapes_out, th_mid, rot, s0, adv, i))
    ink = [p for p in placements if p.shapes]
    upright = (sum(1 for p in ink if _up_y(p, eff, up_out, cw) >= -1e-9) / len(ink)) if ink else 1.0
    if upright < 0.5 and mode != TextMode.RADIAL:
        warnings.append("TEXT_MOSTLY_UPSIDE_DOWN: choose BOTTOM_AUTO_FLIP or INWARD_READABLE for bottom text")
    end_angle = theta0 + sgn * span_deg
    return CurvedText(text, mode.value, placements, radius_mm, center, theta0, end_angle, span_deg, cw, not up_out,
                      cap_h, upright, inner_r if inner_r < 1e17 else radius_mm, outer_r, warnings)


def _up_y(p: GlyphPlacement, eff, up_out, cw) -> float:
    """Screen-space y component (positive = pointing to top of screen) of the glyph's up vector."""
    th = math.radians(p.center_angle_deg)
    if eff == TextMode.RADIAL:
        return math.cos(th)  # ccw tangent y component
    ry = math.sin(th)
    return ry if up_out else -ry


def text_envelope(ct: CurvedText) -> dict:
    return {"inner_radius_mm": ct.inner_radius_mm, "outer_radius_mm": ct.outer_radius_mm,
            "band_width_mm": ct.outer_radius_mm - ct.inner_radius_mm}


def counters_preserved(ct: CurvedText, text: str | None = None) -> dict:
    """Return {char: n_counters} so callers/tests can assert O/A/B/P/R/D/Q keep their holes."""
    return {p.char: sum(len(s.holes) for s in p.shapes) for p in ct.placements if p.shapes}
