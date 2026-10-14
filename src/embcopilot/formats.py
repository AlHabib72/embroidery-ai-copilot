"""
Machine-format exporters/parsers behind one registry, each with a truthful status.

  DST  IMPLEMENTED  (documented Tajima format; project writer + reader; round-trip tested)
  EXP  EXPERIMENTAL (Melco EXP per the commonly documented byte convention; round-trip tested
                     against OUR reader only - NOT verified with third-party software or machines)
  PES  UNAVAILABLE  (Brother PES/PEC stores thread colours as indices into Brother's proprietary
                     colour table, which this project does not ship, plus a bitmap thumbnail block;
                     writing it without both would produce files that misrepresent colours)
  JEF  UNAVAILABLE  (same reason: Janome colour-table indices)
  EMB  UNAVAILABLE  (Wilcom native; see emb.py)

Nothing here renames an extension. Every exporter validates by reading its own file back.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from .models import Design, Record, StitchKind
from .stitch import COLOR_CHANGE, END, JUMP, STITCH, TRIM, StitchPlan, to_dst_design


@dataclass
class FormatCapability:
    fmt: str
    status: str  # IMPLEMENTED | EXPERIMENTAL | UNAVAILABLE
    can_export: bool
    reason: str
    settings: dict = field(default_factory=dict)

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class ExportSettings:
    max_stitch_mm: float = 12.1
    max_design_mm: tuple = (400.0, 400.0)


# ------------------------------------------------------------------ EXP
def write_exp(design: Design, path: str) -> None:
    """Stream of (dx, dy) signed bytes in 0.1 mm; control = 0x80 <code> dx dy.
    codes: 0x01 colour change, 0x04 jump/move, 0x80 trim. y-up (same as DST records)."""
    out = bytearray()
    sb = lambda v: v & 0xFF  # noqa: E731
    for r in design.records:
        dx, dy = r.dx, r.dy
        if r.kind == StitchKind.STITCH:
            if abs(dx) > 127 or abs(dy) > 127:
                raise ValueError("EXP stitch exceeds 12.7mm")
            out += bytes([sb(dx), sb(dy)])
        elif r.kind == StitchKind.JUMP:
            # a zero-length jump is the DST trim convention used by this project; EXP has a real trim code
            steps = max(1, math.ceil(max(abs(dx), abs(dy), 1) / 127))
            px = py = 0
            for k in range(1, steps + 1):
                tx, ty = round(dx * k / steps), round(dy * k / steps)
                out += bytes([0x80, 0x04, sb(tx - px), sb(ty - py)])
                px, py = tx, ty
        elif r.kind == StitchKind.COLOR_CHANGE:
            out += bytes([0x80, 0x01, 0x00, 0x00])
        elif r.kind == StitchKind.END:
            pass
    with open(path, "wb") as fh:
        fh.write(bytes(out))


def read_exp(path: str) -> Design:
    data = open(path, "rb").read()
    recs, x, y, i = [], 0, 0, 0
    sg = lambda b: b - 256 if b > 127 else b  # noqa: E731
    while i + 1 < len(data):
        b0, b1 = data[i], data[i + 1]
        if b0 == 0x80:
            if i + 3 >= len(data):
                break
            code, dx, dy = b1, sg(data[i + 2]), sg(data[i + 3])
            i += 4
            if code == 0x04:
                x += dx; y += dy; recs.append(Record(x, y, dx, dy, StitchKind.JUMP))
            elif code == 0x01:
                recs.append(Record(x, y, 0, 0, StitchKind.COLOR_CHANGE))
            elif code == 0x80:
                recs.append(Record(x, y, 0, 0, StitchKind.JUMP))
            else:
                raise ValueError(f"unknown EXP control code 0x{code:02x} at byte {i-4}")
        else:
            dx, dy = sg(b0), sg(b1)
            x += dx; y += dy
            recs.append(Record(x, y, dx, dy, StitchKind.STITCH))
            i += 2
    recs.append(Record(x, y, 0, 0, StitchKind.END))
    return Design(records=recs, header={}, source=path)


# ------------------------------------------------------------ verification
def compare_designs(a: Design, b: Design) -> dict:
    def summ(d):
        st = [r for r in d.records if r.kind == StitchKind.STITCH]
        # machine files store relative moves only: compare shape, not absolute origin
        ox, oy = (st[0].x, st[0].y) if st else (0, 0)
        return {"stitches": len(st), "color_changes": sum(r.kind == StitchKind.COLOR_CHANGE for r in d.records),
                "xy": [(r.x - ox, r.y - oy) for r in st]}
    sa, sb_ = summ(a), summ(b)
    issues = []
    if sa["stitches"] != sb_["stitches"]:
        issues.append(f"stitch count {sa['stitches']} -> {sb_['stitches']}")
    if sa["color_changes"] != sb_["color_changes"]:
        issues.append(f"colour changes {sa['color_changes']} -> {sb_['color_changes']}")
    if not issues:
        worst = max((max(abs(p[0] - q[0]), abs(p[1] - q[1])) for p, q in zip(sa["xy"], sb_["xy"])), default=0)
        if worst > 0:
            issues.append(f"coordinate drift up to {worst} units (0.1mm)")
    return {"ok": not issues, "issues": issues, "stitches": sb_["stitches"], "color_changes": sb_["color_changes"]}


def capabilities() -> dict[str, FormatCapability]:
    return {
        "dst": FormatCapability("dst", "IMPLEMENTED", True, "documented Tajima format; project writer+reader, round-trip verified"),
        "exp": FormatCapability("exp", "EXPERIMENTAL", True, "Melco EXP per commonly documented convention; verified against this project's reader only"),
        "pes": FormatCapability("pes", "UNAVAILABLE", False, "needs Brother PEC colour table + thumbnail block, not shipped; a guessed file would misstate colours"),
        "jef": FormatCapability("jef", "UNAVAILABLE", False, "needs Janome colour table, not shipped"),
        "emb": FormatCapability("emb", "UNAVAILABLE", False, "Wilcom native; requires a supported Wilcom API/workflow (EMB_API_NOT_CONFIGURED)"),
    }


class ExportError(Exception):
    pass


def export_design(plan: StitchPlan, fmt: str, path: str, settings: ExportSettings | None = None, validation=None) -> dict:
    """Export + immediately read back + compare. Returns a verification record or raises ExportError.
    Refuses unavailable formats instead of writing anything."""
    settings = settings or ExportSettings()
    caps = capabilities()
    fmt = fmt.lower()
    cap = caps.get(fmt)
    if cap is None or not cap.can_export:
        raise ExportError(f"{fmt.upper()}_UNAVAILABLE: {cap.reason if cap else 'unknown format'}")
    if validation is not None and validation.status == "ERROR":
        raise ExportError("VALIDATION_ERRORS: " + "; ".join(f"{f.code}" for f in validation.findings if f.severity == "ERROR")[:200])
    W, H = plan.canvas_mm
    if W > settings.max_design_mm[0] or H > settings.max_design_mm[1]:
        raise ExportError(f"DESIGN_EXCEEDS_LIMIT: {W:.0f}x{H:.0f}mm > {settings.max_design_mm}")
    des = to_dst_design(plan)
    from .dst import read_dst, write_dst
    if fmt == "dst":
        write_dst(des, path, "COPILOT"); back = read_dst(path)
    else:
        write_exp(des, path); back = read_exp(path)
    v = compare_designs(des, back)
    if not v["ok"]:
        try:
            os.remove(path)
        except OSError:
            pass
        raise ExportError("EXPORT_VERIFICATION_FAILED: " + "; ".join(v["issues"]))
    return {"format": fmt, "status": cap.status, "path": path, "bytes": os.path.getsize(path), "verified": True,
            "verification": "read back with this project's own parser and compared stitch count, colour changes and coordinates",
            "caveat": "" if fmt == "dst" else "verified against this project's reader only; open in your embroidery software before sewing"}
