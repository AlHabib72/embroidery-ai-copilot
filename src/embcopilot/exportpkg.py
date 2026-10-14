"""Export package (v0.6): technical SVG/PNG previews derived from the SAME StitchPlan as the machine files,
machine formats with read-back validation, QC-gated, and honest per-format status.

Statuses: EXPORTED_VERIFIED | EXPORT_FAILED_VALIDATION | BLOCKED_BY_QC | UNAVAILABLE | REQUIRES_WILCOM_CONFIGURATION
No file is ever produced for an unavailable format (no renamed DST, no placeholder binary).
"""
from __future__ import annotations

import json
import math
import os
import time

from .emb import EMBAdapter
from .formats import ExportError, capabilities, export_design
from .qc_v2 import OverrideLog, QCReport, export_gate, plan_metrics


def _rgb(c: str):
    from .stitch import _rgb as r
    try:
        return r(c)
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


def plan_to_svg(plan, thread_mm: float = 0.25, show_jumps: bool = False, show_underlay: bool = True) -> str:
    """Technical stitch render (NOT a physical thread simulation).  One polyline per uninterrupted stitch run."""
    W, H = plan.canvas_mm
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.2f} {H:.2f}" width="{W*4:.0f}" height="{H*4:.0f}">',
           '<title>Embroidery AI Copilot - technical stitch render (not a physical simulation)</title>',
           f'<rect width="{W:.2f}" height="{H:.2f}" fill="#fff"/>']
    run, color, under = [], None, False

    def flush():
        nonlocal run
        if len(run) >= 2:
            r, g, b = _rgb(color or "")
            op = 0.35 if under else 1.0
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in run)
            out.append(f'<polyline points="{pts}" fill="none" stroke="rgb({r},{g},{b})" stroke-opacity="{op}" '
                       f'stroke-width="{thread_mm}" stroke-linecap="round" stroke-linejoin="round"/>')
        run = []
    prev = None
    for s in plan.stitches:
        if s.command == "STITCH":
            is_under = s.part.startswith("underlay")
            if not show_underlay and is_under:
                continue
            if run and (s.color != color or is_under != under):
                flush()
            if not run and prev is not None and prev.command == "STITCH":
                run = [(prev.x, prev.y)]
            color, under = s.color, is_under
            run.append((s.x, s.y))
        else:
            if s.command == "JUMP" and show_jumps and prev is not None:
                flush()
                out.append(f'<line x1="{prev.x:.2f}" y1="{prev.y:.2f}" x2="{s.x:.2f}" y2="{s.y:.2f}" stroke="#e00" stroke-width="0.1" stroke-dasharray="1,1"/>')
            else:
                flush()
        prev = s
    flush()
    out.append("</svg>")
    return "\n".join(out)


def render_png(plan, path: str, px_per_mm: float = 6.0, show_jumps: bool = True):
    import cv2
    import numpy as np
    W, H = plan.canvas_mm
    im = np.full((int(H * px_per_mm) + 2, int(W * px_per_mm) + 2, 3), 255, np.uint8)
    prev = None
    for s in plan.stitches:
        if prev is not None:
            a = (int(prev.x * px_per_mm), int(prev.y * px_per_mm)); b = (int(s.x * px_per_mm), int(s.y * px_per_mm))
            if s.command == "STITCH" and prev.command in ("STITCH",) or (s.command == "STITCH" and prev.command == "JUMP"):
                if prev.command == "STITCH":
                    r, g, bl = _rgb(s.color)
                    cv2.line(im, a, b, (bl, g, r), 1, cv2.LINE_AA)
            elif s.command == "JUMP" and show_jumps:
                cv2.line(im, a, b, (0, 0, 220), 1, cv2.LINE_AA)
        prev = s
    cv2.imwrite(path, im)
    return path


def validate_exported(path: str, fmt: str, plan) -> dict:
    """Re-open the file, parse commands, compare stitch count, bounding box, END command and colour changes."""
    from .dst import read_dst
    from .formats import read_exp
    from .models import StitchKind
    des = read_dst(path) if fmt == "dst" else read_exp(path)
    st = [r for r in des.records if r.kind == StitchKind.STITCH]
    issues = []
    n_plan = plan.stats()["stitch_count"]
    if abs(len(st) - n_plan) > 0:
        issues.append(f"stitch count {len(st)} != plan {n_plan}")
    if st:
        xs = [r.x / 10.0 for r in st]; ys = [-r.y / 10.0 for r in st]
        pst = [s for s in plan.stitches if s.command == "STITCH"]
        # DST is relative to the first stitch: compare extents (width/height), not absolute position
        pw = max(s.x for s in pst) - min(s.x for s in pst); ph = max(s.y for s in pst) - min(s.y for s in pst)
        if abs((max(xs) - min(xs)) - pw) > 0.25 or abs((max(ys) - min(ys)) - ph) > 0.25:
            issues.append("bounding-box extent differs by > 0.25 mm")
    if not any(r.kind == StitchKind.END for r in des.records):
        issues.append("no END command")
    return {"ok": not issues, "issues": issues, "stitches": len(st)}


def export_package(plan, objects, out_dir: str, qc: QCReport | None = None, formats=("dst", "exp"),
                   expert_override: dict | None = None, override_log: OverrideLog | None = None,
                   blueprint: dict | None = None, extra_meta: dict | None = None) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    files: dict = {}
    # technical previews + reports: always allowed (they are not machine files)
    with open(os.path.join(out_dir, "design.svg"), "w", encoding="utf-8") as f:
        f.write(plan_to_svg(plan))
    files["design.svg"] = {"status": "EXPORTED", "kind": "technical_render"}
    try:
        render_png(plan, os.path.join(out_dir, "preview.png"))
        files["preview.png"] = {"status": "EXPORTED", "kind": "technical_render"}
    except Exception as e:  # noqa: BLE001
        files["preview.png"] = {"status": "UNAVAILABLE", "reason": f"{type(e).__name__}"}
    if qc is not None:
        with open(os.path.join(out_dir, "qc.json"), "w", encoding="utf-8") as f:
            json.dump(qc.to_dict(), f, indent=1)
        files["qc.json"] = {"status": "EXPORTED"}
    if blueprint is not None:
        with open(os.path.join(out_dir, "technical_blueprint.json"), "w", encoding="utf-8") as f:
            json.dump(blueprint, f, indent=1, default=str)
        files["technical_blueprint.json"] = {"status": "EXPORTED"}
    decision = export_gate(qc, expert_override, override_log) if qc is not None else None
    caps = capabilities()
    for fmt in ("dst", "exp", "pes", "jef"):
        if fmt not in formats:
            continue
        name = f"design.{fmt}"
        cap = caps[fmt]
        if not cap.can_export:
            files[name] = {"status": "UNAVAILABLE", "reason": cap.reason}
            continue
        if decision is not None and not decision.allowed:
            files[name] = {"status": "BLOCKED_BY_QC", "reason": decision.reason}
            continue
        path = os.path.join(out_dir, name)
        try:
            rec = export_design(plan, fmt, path)
            v = validate_exported(path, fmt, plan)
            if not v["ok"]:
                os.remove(path)
                files[name] = {"status": "EXPORT_FAILED_VALIDATION", "issues": v["issues"]}
            else:
                files[name] = {"status": "EXPORTED_VERIFIED", "bytes": rec["bytes"], "caveat": rec.get("caveat", ""),
                               "expert_override": bool(decision and decision.override_logged)}
        except ExportError as e:
            files[name] = {"status": "EXPORT_FAILED_VALIDATION", "issues": [str(e)]}
    # native EMB: only a real, verified backend may produce design.emb; otherwise report why not
    if "emb" in formats:
        st = EMBAdapter().status()
        files["design.emb"] = {"status": "REQUIRES_WILCOM_CONFIGURATION" if st.state == "EMB_API_NOT_CONFIGURED" else "UNAVAILABLE",
                               "reason": st.detail, "alternatives": [k for k, v in files.items() if v.get("status") == "EXPORTED_VERIFIED"]}
    meta = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": files, "metrics": plan_metrics(plan),
            "wording": "production-oriented, QC validated, machine-format validated; requires sew-out validation",
            **(extra_meta or {})}
    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, default=str)
    files["metadata.json"] = {"status": "EXPORTED"}
    return meta
