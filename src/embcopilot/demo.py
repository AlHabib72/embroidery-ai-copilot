"""End-to-end demo (spec 72/73). Generates every stage artifact from a bundled SYNTHETIC reference.

HONESTY: the reference is drawn by code (not professional embroidery artwork); the 'approval' step in the demo is an
automatic, clearly labelled stand-in so exports can be produced unattended - in the dashboard a human must approve.
No PES/JEF/EMB files are produced because those capabilities are UNAVAILABLE (see exports/README.txt)."""

from __future__ import annotations

import json
import os
import shutil

from . import __version__

_COL = {"leaf": (60, 160, 60), "petal": (200, 120, 200), "stem": (30, 100, 30), "decoration": (200, 60, 60), "text": (180, 120, 0),
        "eye": (0, 0, 0), "small_detail": (120, 120, 120), "body": (90, 90, 200), "unknown": (150, 150, 150), "outline": (30, 30, 30), "background": (220, 220, 220)}


def make_reference(path: str, size: int = 600) -> None:
    import cv2
    import numpy as np
    img = np.full((size, size, 3), 255, np.uint8); s = size / 600
    P = lambda x, y: (int(x * s), int(y * s))  # noqa: E731
    cv2.line(img, P(150, 560), P(330, 330), (30, 90, 30), int(9 * s), cv2.LINE_AA)                 # stem
    for c, a in [((225, 450), 25), ((270, 380), -35)]:                                              # leaves on the stem
        cv2.ellipse(img, P(*c), (int(55 * s), int(22 * s)), a, 0, 360, (50, 160, 50), -1, cv2.LINE_AA)
    for k in range(6):                                                                              # flower: 6 petals + centre
        ang = k * 60
        cx, cy = 330 + 62 * np.cos(np.radians(ang)), 250 + 62 * np.sin(np.radians(ang))
        cv2.ellipse(img, P(cx, cy), (int(50 * s), int(24 * s)), ang, 0, 360, (200, 60, 170), -1, cv2.LINE_AA)
    cv2.circle(img, P(330, 250), int(26 * s), (0, 170, 230), -1, cv2.LINE_AA)
    cv2.circle(img, P(480, 470), int(85 * s), (0, 0, 170), -1, cv2.LINE_AA)                          # badge ring
    cv2.circle(img, P(480, 470), int(52 * s), (255, 255, 255), -1, cv2.LINE_AA)
    cv2.putText(img, "A", P(450, 500), cv2.FONT_HERSHEY_SIMPLEX, 2.0 * s, (150, 60, 0), max(2, int(6 * s)), cv2.LINE_AA)
    cv2.imwrite(path, img)


def _annotate(ref_bgr, res, mode):
    import cv2
    import numpy as np
    H, W = ref_bgr.shape[:2]
    sx, sy = W / res.plan.canvas_mm[0], H / res.plan.canvas_mm[1]
    img = np.full_like(ref_bgr, 255) if mode != "overlay" else ref_bgr.copy()
    order = {o: i + 1 for i, o in enumerate(res.plan.order)}
    plans = {p.object_id: p for p in res.technical.plans}
    for o in sorted(res.objects, key=lambda o: o.z_order):
        f = lambda pts: np.array([[p[0] * sx, p[1] * sy] for p in pts], np.int32)  # noqa: E731
        col = _COL.get(o.category, (150, 150, 150))[::-1] if mode in ("semantic",) else tuple(int(v) for v in (0, 0, 0))
        if mode == "objects":
            try:
                r, g, b = (int(v) for v in o.color_group[4:-1].split(","))
            except Exception:
                r, g, b = 128, 128, 128
            cv2.fillPoly(img, [f(o.geometry)], (b, g, r))
            for h in o.holes_mm:
                cv2.fillPoly(img, [f(h)], (255, 255, 255))
            cv2.polylines(img, [f(o.geometry)], True, (0, 0, 0), 1)
        elif mode == "semantic":
            cv2.fillPoly(img, [f(o.geometry)], col)
            for h in o.holes_mm:
                cv2.fillPoly(img, [f(h)], (255, 255, 255))
        elif mode == "technical":
            cv2.polylines(img, [f(o.geometry)], True, (40, 40, 40), 2)
        cx, cy = int(sum(p[0] for p in o.geometry) / len(o.geometry) * sx), int(sum(p[1] for p in o.geometry) / len(o.geometry) * sy)
        if mode == "semantic":
            txt = f"{o.category} {round((o.confidence.get('semantic').value if 'semantic' in o.confidence else 0) * 100)}%"
        elif mode == "technical":
            p = plans[o.object_id]; st = res.plan.object_status[o.object_id]["strategy"]
            txt = f"{st} {p.density_mm}mm"
        elif mode == "sequence":
            txt = str(order.get(o.object_id, "-"))
        else:
            txt = ""
        if txt:
            cv2.putText(img, txt, (max(2, cx - 40), cy), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.putText(img, txt, (max(2, cx - 40), cy), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    return img


def make_demo(out_dir: str, profile: str = "balanced", fabric: str = "T_SHIRT", size_mm: float = 100.0, brand: str = "Generic-24") -> dict:
    import cv2
    import numpy as np
    from .design import design_from_result
    from .formats import export_design
    from .pipeline import run_pipeline
    from .repair import QCTargets, apply_repairs, metrics, propose_repairs, run_qc
    from .simulate import html_player, render
    from .stitch import validate_stitch_plan

    os.makedirs(out_dir, exist_ok=True)
    ref = os.path.join(out_dir, "reference.png")
    make_reference(ref)
    work = os.path.join(out_dir, "_work"); shutil.rmtree(work, ignore_errors=True)
    res = run_pipeline(ref, (size_mm, size_mm), profile, fabric, work, brand=brand, hoop="generic-130x180",
                       overrides={"decoration": "puff"})
    if res.stopped:
        raise RuntimeError(res.stopped)
    H = cv2.imread(ref)
    cv2.imwrite(os.path.join(out_dir, "01_original.png"), H)
    cv2.imwrite(os.path.join(out_dir, "02_segmentation.png"), cv2.imread(os.path.join(work, "03_segmentation.png")))
    cv2.imwrite(os.path.join(out_dir, "03_objects.png"), _annotate(H, res, "objects"))
    cv2.imwrite(os.path.join(out_dir, "04_semantic_blueprint.png"), _annotate(H, res, "semantic"))
    cv2.imwrite(os.path.join(out_dir, "05_technical_blueprint.png"), _annotate(H, res, "technical"))
    # thread mapping card
    cw = res.colorway
    card = np.full((40 + 34 * max(1, len(cw.entries)), 720, 3), 255, np.uint8)
    cv2.putText(card, f"{cw.brand} / {cw.mode} - DeltaE2000 (screen-referred, not sewn appearance)", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    for i, e in enumerate(cw.entries):
        y = 40 + 34 * i
        r, g, b = (int(v) for v in e.design_color[4:-1].split(","))
        cv2.rectangle(card, (8, y), (48, y + 26), (b, g, r), -1); cv2.rectangle(card, (60, y), (100, y + 26), tuple(int(e.thread.hex[k:k + 2], 16) for k in (5, 3, 1)), -1)
        cv2.putText(card, f"{e.thread.code} {e.thread.name}  dE={e.delta_e}  ({e.thread.brand})", (112, y + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(os.path.join(out_dir, "06_thread_mapping.png"), card)
    cv2.imwrite(os.path.join(out_dir, "07_stitch_preview.png"), render(res.plan, "thread", 7))
    seq = render(res.plan, "path", 7)
    cv2.imwrite(os.path.join(out_dir, "08_sequence_preview.png"), seq)

    # QC + auto-repair (proposals are simulated; demo applies those that QC accepted, labelled as demo approval)
    tg = QCTargets(max_jumps=30, max_trims=6)
    ctx = {"objects": res.objects, "technical": res.technical, "canvas": res.plan.canvas_mm, "deps": res.graph.dependencies(), "profile": profile}
    issues = run_qc(res.plan, res.objects, res.validation, tg, res.technical)
    props = propose_repairs(res.plan, issues, ctx, res.objects, tg)
    before = metrics(res.plan)
    plan2, applied = apply_repairs(res.plan, props, [p.name for p in props if p.status == "PROPOSED"], ctx)
    val2 = validate_stitch_plan(plan2, res.objects, ctx["deps"])
    final_plan = plan2 if val2.status != "ERROR" else res.plan
    after = metrics(final_plan)
    res.plan = final_plan
    cv2.imwrite(os.path.join(out_dir, "07_stitch_preview.png"), render(final_plan, "thread", 7))
    cv2.imwrite(os.path.join(out_dir, "08_sequence_preview.png"), render(final_plan, "path", 7))
    with open(os.path.join(out_dir, "playback.html"), "w", encoding="utf-8") as fh:
        fh.write(html_player(final_plan, res.objects))
    # comparison picture: reference vs stitched coverage overlay
    from .compare_geo import _coverage_mask
    sc = 6.0; size = (int(size_mm * sc) + 2, int(size_mm * sc) + 2)
    cov = _coverage_mask(final_plan, sc, size)
    refm = cv2.resize(res.blueprint.debug_images["foreground_mask"], size, interpolation=cv2.INTER_NEAREST) > 0
    cmp_img = np.full((size[1], size[0], 3), 255, np.uint8)
    cmp_img[refm & cov] = (90, 170, 90); cmp_img[refm & ~cov] = (60, 60, 220); cmp_img[~refm & cov] = (40, 140, 240)
    cv2.putText(cmp_img, "green=covered  red=missing  orange=extra stitching", (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(os.path.join(out_dir, "10_comparison.png"), cmp_img)

    # exports (only what is really available, each verified by read-back)
    ex_dir = os.path.join(out_dir, "exports"); os.makedirs(ex_dir, exist_ok=True)
    fv = val2 if final_plan is plan2 else res.validation
    exports = {f: export_design(final_plan, f, os.path.join(ex_dir, f"design.{f}"), validation=fv) for f in ("dst", "exp")}
    from .formats import capabilities
    un = {k: v.reason for k, v in capabilities().items() if not v.can_export}
    open(os.path.join(ex_dir, "README.txt"), "w").write(
        "design.dst  IMPLEMENTED, verified by read-back\ndesign.exp  EXPERIMENTAL, verified against this project's reader only\n\nNOT produced (UNAVAILABLE, no fake files written):\n"
        + "".join(f"  design.{k}: {v}\n" for k, v in un.items()))
    design = design_from_result(res, "reference.png", fabric, profile)
    open(os.path.join(ex_dir, "design.ecd.json"), "w").write(design.to_json())

    os.makedirs(os.path.join(out_dir, "reports"), exist_ok=True)
    final = {
        "version": __version__, "reference": "synthetic, drawn by code", "profile": profile, "fabric": fabric, "vision_mode": res.vision_mode,
        "objects": [{"name": o.name, "semantic": o.category, "semantic_confidence": o.confidence.get("semantic").value if "semantic" in o.confidence else None,
                     "stitch": res.plan.object_status.get(o.object_id, {}).get("strategy"), "risk": next(a.risk for a in res.assessments if a.object_id == o.object_id),
                     "match": next((c.status for c in res.comparison if c.object_id == o.object_id), None)} for o in res.objects],
        "stats_computed_from_stitches": final_plan.stats(), "validation": val2.to_dict() if final_plan is plan2 else res.validation.to_dict(),
        "qc_issues": [i.to_dict() for i in issues], "repair": {"proposals": [p.to_dict() for p in props], "applied_in_demo": applied, "before": before, "after": after,
                                                               "note": "applied automatically ONLY in this demo; the dashboard requires a human to apply each repair"},
        "comparison": res.comparison_global, "hoop": res.hoop_check, "threads": cw.to_dict(), "exports": exports, "unavailable": un,
        "approval": "DEMO STAND-IN: a real workflow requires explicit human approval before export", "timings_s": res.timings, "human_review": "required"}
    with open(os.path.join(out_dir, "reports", "final_report.json"), "w") as fh:
        json.dump(final, fh, indent=1, default=str)
    st = final_plan.stats()
    rows = "".join(f"<tr><td>{o['name']}</td><td>{o['semantic']} ({round((o['semantic_confidence'] or 0)*100)}%)</td><td>{o['stitch']}</td><td>{o['risk']}</td><td>{o['match']}</td></tr>" for o in final["objects"])
    iss = "".join(f"<li>{i.severity} {i.code}: {i.message}</li>" for i in issues) or "<li>none</li>"
    imgs = "".join(f'<figure style="display:inline-block;margin:4px"><img width=280 src="../{n}"><figcaption>{c}</figcaption></figure>' for n, c in
                   [("01_original.png", "Reference"), ("02_segmentation.png", "Segmentation"), ("04_semantic_blueprint.png", "Semantic blueprint"),
                    ("05_technical_blueprint.png", "Technical blueprint"), ("07_stitch_preview.png", "Stitch preview (SIMULATION)"), ("10_comparison.png", "Reference comparison")])
    html = f"""<!doctype html><meta charset=utf-8><title>Final report</title><style>body{{font:14px sans-serif;margin:20px}}td,th{{border:1px solid #bbb;padding:4px 8px}}table{{border-collapse:collapse}}</style>
<h2>Embroidery AI Copilot {__version__} - demo final report</h2><p><b>Synthetic reference. Human review required.</b> Previews are SIMULATION; approval in this demo is an automatic stand-in.</p>{imgs}
<h3>Objects</h3><table><tr><th>Object</th><th>Semantic (heuristic)</th><th>Stitch</th><th>Risk</th><th>Reference match</th></tr>{rows}</table>
<h3>Computed from the generated stitches</h3><pre>{json.dumps({k: v for k, v in st.items() if k != 'ESTIMATE_ASSUMPTIONS'})}</pre>
<h3>QC</h3><ul>{iss}</ul><p>Repair (before/after): {json.dumps(before)} -> {json.dumps(after)}; applied in demo: {applied}</p>
<h3>Exports</h3><pre>{json.dumps({k: {kk: vv for kk, vv in v.items() if kk in ('format','status','bytes','verified','caveat')} for k, v in exports.items()}, indent=1)}</pre>
<p>Not produced (UNAVAILABLE): {', '.join(un)}</p><p><a href="../playback.html">Stitch playback</a></p>"""
    open(os.path.join(out_dir, "reports", "final_report.html"), "w", encoding="utf-8").write(html)
    qc_html = f"<!doctype html><meta charset=utf-8><h2>QC report</h2><p>Validation: {val2.status if final_plan is plan2 else res.validation.status}</p><ul>{iss}</ul>"
    open(os.path.join(out_dir, "09_qc_report.html"), "w", encoding="utf-8").write(qc_html)
    shutil.rmtree(work, ignore_errors=True)
    return final
