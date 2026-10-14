"""EMB Studio (v0.7): honest, separate EMB capabilities + safe file inspection + object-level HANDOFF bundle.

What this module does NOT do: create, edit, convert to, or fully parse native .EMB (Wilcom proprietary).  No fake files, no reverse engineering.
What it DOES: (1) report six independent EMB capabilities, (2) inspect an uploaded .emb for facts that need no decoding and reject
renamed DST/PES/ZIP files, (3) build a handoff bundle (verified DST + per-object SVG + parameter manifest) that lets a digitizer
produce the real EMB inside Wilcom without re-digitizing from scratch.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re

MAX_EMB_BYTES = 50 * 1024 * 1024
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def emb_capabilities(backend_cap: dict | None = None) -> dict:
    from .nativeemb import NativeEMBAdapter
    cap = backend_cap or NativeEMBAdapter().detect_capability()
    st = cap["state"]
    native = ("REQUIRES_WILCOM_CONFIGURATION" if st == "REQUIRES_WILCOM_CONFIGURATION"
              else "CONFIGURED_PROTOCOL_NOT_IMPLEMENTED" if st == "CONFIGURED_PROTOCOL_NOT_IMPLEMENTED" else "UNAVAILABLE")
    why = cap.get("reason", "")
    row = lambda s, r, req="": {"state": s, "reason": r, "requires": req}  # noqa: E731
    return {
        "EMB_INSPECTION": row("PARTIAL", "file-level facts only (size, hash, container signature, undecoded strings); no object model is read"),
        "EMB_IMPORT": row("UNAVAILABLE", "stitches/objects of a native EMB cannot be read here. Export DST/PES from Wilcom and import that instead (QC works on DST)",
                          "supported Wilcom route"),
        "EMB_EDIT": row(native, why or "needs a supported native integration", "configured + verified Wilcom backend"),
        "EMB_CREATE": row(native, why or "needs a supported native integration", "configured + verified Wilcom backend"),
        "EMB_SAVE": row(native, why or "needs a supported native integration", "configured + verified Wilcom backend"),
        "EMB_EXPORT": row(native, why or "needs a supported native integration; no .emb is produced without verification (reopen/object count/dimensions)",
                          "configured + verified Wilcom backend"),
        "handoff_bundle": row("IMPLEMENTED", "verified DST + per-object SVG + parameter manifest for rebuilding objects in Wilcom (NOT an EMB file)"),
        "backend": cap.get("backend"), "ui_button": cap.get("button"),
        "message": "Native EMB creation requires a configured supported Wilcom/native integration.",
    }


def _strings(data: bytes, min_len: int = 6, limit: int = 30) -> list[str]:
    out, seen = [], set()
    for m in re.finditer(rb"[ -~]{%d,}" % min_len, data[:2_000_000]):
        s = m.group(0).decode("ascii")
        if s not in seen:
            seen.add(s); out.append(s)
    for m in re.finditer(rb"(?:[ -~]\x00){%d,}" % min_len, data[:2_000_000]):
        s = m.group(0).decode("utf-16le", "ignore")
        if s not in seen:
            seen.add(s); out.append(s)
    return out[:limit]


def _entropy(b: bytes) -> float:
    if not b:
        return 0.0
    from collections import Counter
    n = len(b)
    return -sum(c / n * math.log2(c / n) for c in Counter(b[:262144]).values())


def inspect_emb_bytes(data: bytes, filename: str = "upload.emb") -> dict:
    """Safe inspection of an uploaded .emb.  Never parses an object model; never executes anything; bounded work."""
    name = os.path.basename(filename or "")[:120]
    rep: dict = {"filename": name, "bytes": len(data), "classification": "UNKNOWN_BINARY", "native_emb_confirmed": False,
                 "object_level_data": "NOT_READABLE (no native integration)", "capabilities": emb_capabilities(), "warnings": []}
    if not name.lower().endswith(".emb"):
        rep["warnings"].append("EXTENSION_NOT_EMB")
    if len(data) == 0:
        rep.update(classification="EMPTY", ok=False); return rep
    if len(data) > MAX_EMB_BYTES:
        rep.update(classification="TOO_LARGE", ok=False, warnings=rep["warnings"] + [f"limit is {MAX_EMB_BYTES // (1024*1024)} MB"]); return rep
    rep["sha256"] = hashlib.sha256(data).hexdigest()
    rep["first_16_bytes_hex"] = data[:16].hex()
    rep["entropy_bits_per_byte"] = round(_entropy(data), 2)
    head = data[:512]
    if head[:3] == b"LA:":
        rep.update(classification="RENAMED_DST", ok=False)
        rep["warnings"].append("This is a Tajima DST file with an .emb extension, not a native EMB. Rename it to .dst to run QC on it.")
    elif data[:4] == b"#PES":
        rep.update(classification="RENAMED_PES", ok=False)
        rep["warnings"].append("This is a Brother PES file with an .emb extension, not a native EMB.")
    elif data[:2] == b"PK":
        rep.update(classification="ZIP_CONTAINER", ok=True)
        rep["warnings"].append("File starts with a ZIP signature. Nothing was extracted or opened.")
    elif data[:8] == OLE_MAGIC:
        rep.update(classification="OLE_COMPOUND_FILE", ok=True)
        rep["note"] = "File starts with an OLE2 compound-file signature (an observation only; its internal streams were NOT read)."
    else:
        rep["ok"] = True
        if rep["entropy_bits_per_byte"] > 7.5:
            rep["note"] = "High entropy: content looks compressed or encrypted; nothing can be said about its structure without a supported Wilcom route."
    rep.setdefault("ok", True)
    rep["strings_undecoded"] = _strings(data) if rep["classification"] not in ("RENAMED_DST", "RENAMED_PES") else []
    rep["strings_note"] = "printable text found in the bytes; may be unrelated noise; not interpreted as design data"
    rep["next_steps"] = [
        "To edit this design: open the file in Wilcom.",
        "To bring its stitches into this tool: in Wilcom export a DST (or PES), then upload that - QC, simulation and repair work on stitch data.",
        "To get a NEW editable EMB from a design made here: use the handoff bundle (DST + object SVG + parameter manifest) and save as EMB inside Wilcom.",
    ]
    return rep


# --------------------------------------------------------------------------- handoff bundle
def _hex(c: str) -> str:
    m = re.match(r"rgb\((\d+),(\d+),(\d+)\)", c or "")
    return "#%02X%02X%02X" % tuple(int(x) for x in m.groups()) if m else (c if (c or "").startswith("#") else "#000000")


def _path(poly) -> str:
    return "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in poly) + " Z"


def build_object_manifest(build) -> list[dict]:
    plan, tb = build.plan, build.tb
    tbp = {p.object_id: p for p in tb.plans}
    first, last, cnt = {}, {}, {}
    for s in plan.stitches:
        if s.command == "STITCH" and not s.part.startswith("connector"):
            first.setdefault(s.object_id, (round(s.x, 2), round(s.y, 2)))
            last[s.object_id] = (round(s.x, 2), round(s.y, 2))
            cnt[s.object_id] = cnt.get(s.object_id, 0) + 1
    seq = {oid: i + 1 for i, oid in enumerate(plan.order)}
    rows = []
    for o in build.objects:
        p = tbp.get(o.object_id)
        rows.append({"sequence": seq.get(o.object_id), "object_id": o.object_id, "name": o.name, "semantic_type": o.category,
                     "stitch_type": p.stitch_type if p else None, "width_mm": round(p.width_mm, 2) if p else None,
                     "density_mm": p.density_mm if p else None, "underlay": p.underlay if p else None,
                     "pull_compensation_mm": p.pull_compensation_mm if p else None, "angle_deg": p.direction_deg if p else None,
                     "thread_color_hex": _hex(o.color_group), "bbox_mm": [round(v, 2) for v in o.bbox_mm], "holes": len(o.holes_mm or []),
                     "entry_mm": first.get(o.object_id), "exit_mm": last.get(o.object_id), "stitches": cnt.get(o.object_id, 0)})
    rows.sort(key=lambda r: (r["sequence"] is None, r["sequence"] or 0))
    return rows


def objects_svg(build) -> str:
    W, H = build.plan.canvas_mm
    g = []
    for o in build.objects:
        d = _path(o.geometry) + "".join(" " + _path(h) for h in (o.holes_mm or []))
        g.append(f'<path id="{o.object_id}" data-name="{o.name}" data-type="{o.category}" d="{d}" fill="{_hex(o.color_group)}" fill-rule="evenodd" stroke="none"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.2f} {H:.2f}" width="{W:.2f}mm" height="{H:.2f}mm">'
            "<title>Object geometry (mm). Artwork for re-digitizing; NOT a stitch file</title>" + "".join(g) + "</svg>")


def handoff_bundle(build, out_dir: str, name: str = "design", machine: str = "generic_flat") -> dict:
    from .exportpkg import export_package
    os.makedirs(out_dir, exist_ok=True)
    meta = export_package(build.plan, build.objects, out_dir, build.qc, ("dst",), extra_meta={"purpose": "wilcom_handoff"})
    dst_status = meta["files"].get("design.dst", {})
    if os.path.exists(os.path.join(out_dir, "design.dst")):
        os.replace(os.path.join(out_dir, "design.dst"), os.path.join(out_dir, f"{name}.dst"))
    rows = build_object_manifest(build)
    with open(os.path.join(out_dir, "object_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"objects": rows, "fabric": build.tb.fabric, "canvas_mm": build.plan.canvas_mm,
                   "note": "parameters are this tool's engine values (heuristic); re-check them in Wilcom for your fabric/machine"}, f, indent=1)
    with open(os.path.join(out_dir, "object_manifest.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["object_id"])
        w.writeheader(); [w.writerow({k: (json.dumps(v) if isinstance(v, (list, tuple)) else v) for k, v in r.items()}) for r in rows]
    with open(os.path.join(out_dir, "design_objects.svg"), "w", encoding="utf-8") as f:
        f.write(objects_svg(build))
    with open(os.path.join(out_dir, "HANDOFF_README.md"), "w", encoding="utf-8") as f:
        f.write(
            "# Wilcom handoff bundle\n\n**This is NOT an EMB file.** Native .EMB can only be produced by Wilcom (or a supported Wilcom integration).\n\n"
            f"- `{name}.dst` - machine stitches, QC-gated and read-back verified: {dst_status.get('status')}. Stitch-level only.\n"
            "- `design_objects.svg` - one path per object (holes preserved, millimetres). Use it as artwork to rebuild editable objects.\n"
            "- `object_manifest.csv/.json` - per object: sequence, stitch type, width, density, underlay, pull compensation, angle, thread colour, entry/exit.\n\n"
            "## Suggested path to a real EMB\n1. In Wilcom open the DST for a stitch-level reference, or bring in `design_objects.svg` as artwork.\n"
            "2. Re-create/convert objects following `object_manifest.csv` (types, sequence, parameters).\n3. Save as EMB inside Wilcom.\n\n"
            "Values are heuristic engine outputs; validate with a sew-out.\n")
    return {"dir": out_dir, "files": sorted(os.listdir(out_dir)), "dst": dst_status, "objects": len(rows),
            "not_an_emb": True, "wording": "handoff only; native EMB requires Wilcom"}
