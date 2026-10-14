"""v0.6 CLI subcommands (kept in a separate module so the v0.5 CLI stays untouched and backward compatible)."""
from __future__ import annotations

import json
import os
import sys

V06_COMMANDS = {"capabilities", "benchmark", "fonts", "badge", "command", "export-all", "text-check", "ask", "emb-info", "emb-handoff"}


def add_parsers(sub):
    sub.add_parser("capabilities", help="print the honest v0.6 capability states (JSON)")
    b = sub.add_parser("benchmark", help="run the synthetic golden set through the pipeline + QC v2 and write benchmark.json (real timings)")
    b.add_argument("--out-dir", default="benchmark_out"); b.add_argument("--machine", default="generic_flat")
    f = sub.add_parser("fonts", help="font browser: list fonts found in system/user folders")
    f.add_argument("--dir", action="append", default=[]); f.add_argument("--limit", type=int, default=50)
    bd = sub.add_parser("badge", help="build a PARAMETRIC vector badge (rings + circular glyph text + monogram), plan, QC, export package")
    bd.add_argument("--out-dir", default="badge_out"); bd.add_argument("--top", default="LOUIS VUITTON"); bd.add_argument("--bottom", default="PARIS 1854")
    bd.add_argument("--monogram", default="LV"); bd.add_argument("--size-mm", type=float, default=100.0); bd.add_argument("--font", default=None)
    bd.add_argument("--fabric", default="woven"); bd.add_argument("--machine", default="generic_flat")
    bd.add_argument("--expert-override-reason", default=None, help="export despite QC errors (logged)")
    c = sub.add_parser("command", help="parse a (English/Hinglish) multi-step command into a validated plan; executes nothing")
    c.add_argument("text")
    e = sub.add_parser("export-all", help="artwork -> pipeline -> production pass -> QC-gated, read-back-validated export package")
    e.add_argument("input"); e.add_argument("--out-dir", default="export_out"); e.add_argument("--size-mm", type=float, default=100.0)
    e.add_argument("--fabric", default="woven"); e.add_argument("--profile", default="balanced"); e.add_argument("--machine", default="generic_flat")
    e.add_argument("--expert-override-reason", default=None)
    a = sub.add_parser("ask", help="v0.7 prompt box on a parametric badge or an artwork: parse -> validate -> preview; add --yes to approve and apply")
    a.add_argument("prompt"); a.add_argument("--input", default=None, help="artwork file (default: built-in vector badge)")
    a.add_argument("--ai-mode", default=os.environ.get("EMBCOPILOT_AI_MODE", "LOCAL")); a.add_argument("--approval", default="CONFIRM")
    a.add_argument("--yes", action="store_true", help="approve the proposal (otherwise preview only)"); a.add_argument("--out-dir", default=None)
    ei = sub.add_parser("emb-info", help="EMB capabilities; with a file path also safe inspection of that .emb"); ei.add_argument("path", nargs="?")
    eh = sub.add_parser("emb-handoff", help="Wilcom handoff bundle (verified DST + object SVG + manifest). NOT an EMB file")
    eh.add_argument("--out-dir", default="emb_handoff"); eh.add_argument("--input", default=None)
    t = sub.add_parser("text-check", help="font suitability analysis for a text at a size")
    t.add_argument("text"); t.add_argument("--font", default=None); t.add_argument("--cap-mm", type=float, default=5.0)


def capabilities_report() -> dict:
    from . import __version__
    from .nativeemb import NativeEMBAdapter
    from .formats import capabilities as fcaps
    try:
        from .segmentation import SAM2Segmentation
        SAM2Segmentation()._predictor(); sam2 = "REQUIRES_CONFIGURATION (importable; never exercised in this build)"
    except Exception:  # noqa: BLE001
        sam2 = "REQUIRES_CONFIGURATION (torch/sam2/checkpoint not available) - falls back to classical CV"
    from .fonts import scan_fonts
    nfonts = len(scan_fonts(limit=5000))
    return {"version": __version__, "formats": {k: v.to_dict() for k, v in fcaps().items()},
            "native_emb": NativeEMBAdapter().detect_capability(),
            "features": {
                "vector_glyph_text (TTF/OTF/TTC via fontTools)": "IMPLEMENTED", "curved_text_engine": "IMPLEMENTED",
                "font_suitability": "PARTIAL (heuristic thresholds)", "fonts_found_on_this_machine": nfonts,
                "input_c_satin_v2 (centerline->rails)": "IMPLEMENTED", "parametric_circle_inference": "IMPLEMENTED",
                "circular_badge_planner": "PARTIAL (heuristic, synthetic-tested)", "negative_space + protected_zones": "IMPLEMENTED",
                "travel_policy + zone rerouting": "PARTIAL", "short_stitch_classification": "IMPLEMENTED",
                "qc_v2 + release gates + export blocking": "IMPLEMENTED", "machine_profiles": "IMPLEMENTED (generic presets)",
                "project_store (.eacproject, undo/redo)": "IMPLEMENTED", "learning_store + calibration": "IMPLEMENTED (opt-in)",
                "multi_step_commands (EN/Hinglish)": "PARTIAL (parse only)", "design_diff": "IMPLEMENTED",
                "SAM2": sam2, "PES": "UNAVAILABLE", "JEF": "UNAVAILABLE", "VP3": "UNAVAILABLE",
                "native_emb": "REQUIRES_WILCOM_CONFIGURATION", "wilcom_automation": "REQUIRES_WILCOM (MOCK only)",
                "physical_sewout_simulation": "UNAVAILABLE", "thread_brand_codes": "REQUIRES_LICENSED_RESOURCE (import your CSV)",
                "react_frontend": "PLANNED (stdlib web server + static dashboard)"}}


def run(args) -> int:
    c = args.command
    if c == "capabilities":
        print(json.dumps(capabilities_report(), indent=2)); return 0
    if c == "benchmark":
        from .golden import benchmark
        r = benchmark(args.out_dir, machine=args.machine)
        for k, v in r["designs"].items():
            if v["ok"]:
                a = v["after_production_pass"]
                print(f"{k}: {v['seconds']}s | stitches {v['metrics']['stitch_count']}->{a['metrics']['stitch_count']} | jumps {v['metrics']['jump_count']}->{a['metrics']['jump_count']} | "
                      f"trims {v['metrics']['trim_count']}->{a['metrics']['trim_count']} | protected-crossings {v['metrics']['center_crossing_travel']}->{a['metrics']['center_crossing_travel']} | "
                      f"QC errors {v['qc_errors']}->{a['qc_errors']}")
            else:
                print(f"{k}: FAILED {v['error']}")
        print(f"wrote {os.path.join(args.out_dir, 'benchmark.json')}"); return 0
    if c == "fonts":
        from .fonts import scan_fonts
        for f in scan_fonts(args.dir, args.limit):
            print(f"{f.family} [{f.style}] glyphs={f.glyphs} kerning={'yes' if f.has_kerning else 'no'}  {f.path}")
        return 0
    if c == "command":
        from .command_v2 import parse_multi_command
        print(json.dumps(parse_multi_command(args.text, ["background", "outer_ring", "inner_ring", "circular_text", "center_logo"]).to_dict(), indent=2)); return 0
    if c == "text-check":
        from .fonts import FontError, analyse_text
        from .golden import find_default_font
        font = args.font or find_default_font()
        try:
            print(json.dumps(analyse_text(font, args.text, args.cap_mm), indent=2))
        except FontError as e:
            print(f"Font problem: {e}", file=sys.stderr); return 2
        return 0
    if c in ("ask", "emb-handoff", "emb-info"):
        return _run_v07(args)
    if c == "badge":
        from .production import production_pass
        from .vectorbadge import build_vector_badge, plan_vector_badge
        from .pipeline import PipelineResult
        try:
            bp, deps, info = build_vector_badge(args.size_mm, args.font, args.top, args.bottom, args.monogram)
        except ValueError as e:
            print(f"Could not build the badge: {e}", file=sys.stderr); return 2
        tb, plan, val = plan_vector_badge(bp, deps, args.fabric)
        R = PipelineResult(bp, bp.objects, None, tb, plan, val, [], {}, [], [], {}, {})
        ov = {"confirm": True, "reason": args.expert_override_reason, "user": "cli"} if args.expert_override_reason else None
        res = production_pass(R, args.machine, args.out_dir, ("dst", "exp", "pes", "emb"), ov)
        with open(os.path.join(args.out_dir, "badge_info.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, indent=1, default=str)
        _summary(res); return 0
    if c == "export-all":
        from .pipeline import run_pipeline
        from .production import production_pass
        R = run_pipeline(args.input, (args.size_mm, args.size_mm), profile=args.profile, fabric=args.fabric)
        if R.stopped:
            print(f"Stopped: {R.stopped}", file=sys.stderr); return 2
        ov = {"confirm": True, "reason": args.expert_override_reason, "user": "cli"} if args.expert_override_reason else None
        res = production_pass(R, args.machine, args.out_dir, ("dst", "exp", "pes", "emb"), ov)
        _summary(res); return 0
    return 1


def _summary(res):
    m = res.diff["metrics"]
    print("BEFORE -> AFTER (production pass)")
    for k in ("stitch_count", "jump_count", "trim_count", "center_crossing_travel", "short_stitch_count"):
        print(f"  {k}: {m[k]['old']} -> {m[k]['new']}")
    print("QC errors:", len(res.qc_after.errors), "| gates:", ", ".join(f"{g['gate'].split(' - ')[0]}={'OK' if g['passed'] else 'FAIL'}" for g in res.gates))
    for k, v in (res.package or {}).get("files", {}).items():
        print(f"  {k}: {v['status']}" + (f" ({v.get('reason','')[:70]})" if v["status"] not in ("EXPORTED", "EXPORTED_VERIFIED") else ""))
    print("wording: production-oriented, QC validated, machine-format validated; requires sew-out validation")


def _session_for(input_path):
    from .ai.session import DesignSession
    if input_path:
        from .pipeline import run_pipeline
        R = run_pipeline(input_path, (100.0, 100.0))
        if R.stopped:
            raise SystemExit(f"Stopped: {R.stopped}")
        return DesignSession.from_pipeline(R)
    from .vectorbadge import build_vector_badge
    bp, deps, _ = build_vector_badge()
    return DesignSession(bp, deps)


def _run_v07(args) -> int:
    c = args.command
    if c == "emb-info":
        from .emb_studio import emb_capabilities, inspect_emb_bytes
        if args.path:
            with open(args.path, "rb") as f:
                data = f.read(60 * 1024 * 1024)
            print(json.dumps(inspect_emb_bytes(data, os.path.basename(args.path)), indent=2, default=str)); return 0
        print(json.dumps(emb_capabilities(), indent=2)); return 0
    if c == "emb-handoff":
        from .emb_studio import handoff_bundle
        r = handoff_bundle(_session_for(args.input).build, args.out_dir)
        print(json.dumps(r, indent=2)); print("NOTE: this is a handoff bundle, NOT an EMB file."); return 0
    from .ai.controller import AIController
    from .ai.providers import load_env
    from .ai.router import AIRouter
    load_env(".env")
    S = _session_for(args.input)
    C = AIController(S, AIRouter(args.ai_mode.upper()), args.approval, args.out_dir)
    p = C.propose(args.prompt)
    r = p["router"]
    print(f"parsed by: {r['provider']} / {r['model']}  ({r['latency_ms']:.0f} ms)  mode={r['mode']}  fallback_to_local={r['fallback_to_local']}")
    for a in r["attempts"]:
        print(f"  attempt {a['provider']}: {'ok' if a['ok'] else a['code']}")
    for ch in p["chips"]:
        print(f"  [{ch['risk']:6}] {ch['label']}")
    for e in p["errors"] + [f"not understood: {u}" for u in p["unrecognised"]]:
        print("  !", e)
    print(f"risk={p['decision']['risk']}  {p['decision']['reason']}  status={p['status']}")
    if p.get("preview") and "after" in p["preview"]:
        b, a = p["preview"]["before"], p["preview"]["after"]
        for k in ("stitch_count", "jump_count", "trim_count", "center_crossing_travel", "short_stitch_count", "qc_errors"):
            print(f"  {k}: {b[k]} -> {a[k]}")
        for sk in p["preview"]["skipped"]:
            print("  skipped:", sk["reason"])
    if args.yes and p["status"] == "pending":
        C.approve(p["proposal_id"])
        print("APPLIED. history:", len(S.history), "step(s); undo available:", bool(S.undo_stack))
    elif p["status"] == "pending":
        print("(preview only; re-run with --yes to apply)")
    return 0
