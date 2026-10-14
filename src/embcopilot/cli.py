#!/usr/bin/env python3
"""
Embroidery AI Copilot CLI.

Phase 1 (unchanged, still the default when no subcommand is given):

    embcopilot design.dst
    embcopilot design.dst --fabric cap
    embcopilot design.dst --fabric cap --spm 900
    embcopilot design.dst --json --output reports/
    embcopilot --list-fabrics
    embcopilot analyze design.dst      (same as the bare form above)
    embcopilot qc design.dst           (alias for analyze)

Phase 2 additions — all of these are advisory/simulation only; none of
them touches a real Wilcom installation (see README "Wilcom support"):

    embcopilot blueprint photo.png --target-width-mm 80 --target-height-mm 80
    embcopilot technical-plan photo.png --target-width-mm 80 --target-height-mm 80 --fabric cap
    embcopilot simulate photo.png --target-width-mm 80 --target-height-mm 80
    embcopilot simulate --dry-run

Reads a Tajima DST, runs the QC analysis, and writes an HTML report (and
optionally JSON) alongside it. The source .dst file is never modified, and
no reference image is ever sent anywhere — everything here runs locally.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .analyze import analyze
from .config import get_fabric_preset, list_fabrics
from .dst import CorruptDSTError, DSTError, InvalidDSTError, read_dst
from .report import render_html, render_json

from .profiles import PROFILES as _PROFILES  # noqa: E402

_PROFILE_HELP = "production profile that changes density/underlay/pull-comp/min-feature (one of: " + ", ".join(sorted(_PROFILES)) + ")"

from .fabrics import FABRICS as _FABRICS  # noqa: E402

_FABRIC_CHOICES = sorted({fp.name for fp in list_fabrics()} | {k.lower() for k in _FABRICS} | set(_FABRICS))

KNOWN_SUBCOMMANDS = {"analyze", "qc", "blueprint", "technical-plan", "simulate", "stitches", "demo", "inspect-emb", "web", "threads", "capabilities", "benchmark", "fonts", "badge", "command", "export-all", "text-check", "ask", "emb-info", "emb-handoff"}


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------


def _add_analyze_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", nargs="?", help="path to a .dst file")
    p.add_argument(
        "--fabric",
        choices=_FABRIC_CHOICES,
        default="woven",
        help="fabric preset controlling QC thresholds (default: woven)",
    )
    p.add_argument("--spm", type=int, default=None, help="override machine speed (stitches per minute)")
    p.add_argument("--output", "-o", default=None, help="output directory (default: alongside the input file)")
    p.add_argument("--html", default=None, help="explicit HTML report path (overrides --output for HTML)")
    p.add_argument("--json", nargs="?", const=True, default=False, help="also write a JSON report; optionally give an explicit path")
    p.add_argument("--verbose", action="store_true", help="print full issue detail to the console")
    p.add_argument("--debug", action="store_true", help="show full Python tracebacks on error")
    p.add_argument("--quiet", action="store_true", help="suppress the console summary")


def _add_image_target_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("image", nargs="?", help="reference image (png/jpg/etc)")
    p.add_argument("--target-width-mm", type=float, default=50.0, help="physical design width in mm (default 50)")
    p.add_argument("--target-height-mm", type=float, default=50.0, help="physical design height in mm (default 50)")
    p.add_argument("--output", "-o", default=None, help="output JSON path")
    p.add_argument("--debug", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="embcopilot",
        description="Embroidery AI Copilot. Phase 1: rule-based DST QC analyzer. "
        "Phase 2: advisory blueprint/technical-plan/simulate tools. "
        "Not an automatic digitizer, and no real Wilcom automation exists yet — see README.md.",
    )
    p.add_argument("--version", action="version", version=f"embcopilot {__version__}")
    p.add_argument("--list-fabrics", action="store_true", help="list fabric presets and exit")

    sub = p.add_subparsers(dest="command")

    analyze_p = sub.add_parser("analyze", help="QC-analyze a .dst file (Phase 1)")
    _add_analyze_args(analyze_p)

    qc_p = sub.add_parser("qc", help="alias for 'analyze'")
    _add_analyze_args(qc_p)

    blueprint_p = sub.add_parser(
        "blueprint",
        help="detect coloured regions in a reference image (Object Blueprint; geometry only, no semantic categories)",
    )
    _add_image_target_args(blueprint_p)
    blueprint_p.add_argument("--debug-dir", default=None, help="write numbered debug artifacts (masks, contours, centerlines, blueprint json) here")

    tech_p = sub.add_parser(
        "technical-plan",
        help="turn a reference image into a per-region digitizing plan (Technical Blueprint)",
    )
    _add_image_target_args(tech_p)
    tech_p.add_argument(
        "--fabric", default="woven", choices=_FABRIC_CHOICES,
        help="fabric profile passed to the knowledge engine",
    )
    tech_p.add_argument("--profile", default="balanced", help=_PROFILE_HELP)

    sim_p = sub.add_parser(
        "simulate",
        help="run a technical plan through the MOCK Wilcom adapter only (no real Wilcom involved)",
    )
    _add_image_target_args(sim_p)
    sim_p.add_argument("--fabric", default="woven", choices=_FABRIC_CHOICES)
    sim_p.add_argument("--profile", default="balanced", help=_PROFILE_HELP)
    sim_p.add_argument("--dry-run", action="store_true", help="print the action plan only; execute nothing")

    st_p = sub.add_parser("stitches", help="generate ACTUAL stitch geometry from a reference image, validate it, write debug artifacts + DST")
    _add_image_target_args(st_p)
    st_p.add_argument("--fabric", default="woven", choices=_FABRIC_CHOICES)
    st_p.add_argument("--profile", default="balanced", help=_PROFILE_HELP)
    st_p.add_argument("--out-dir", default="output/stitches", help="directory for numbered artifacts")
    st_p.add_argument("--json", action="store_true", help="print the summary as JSON")

    demo_p = sub.add_parser("demo", help="run the full pipeline on a bundled synthetic reference and write demo/ artifacts")
    demo_p.add_argument("--out-dir", default="demo")
    demo_p.add_argument("--profile", default="balanced", help=_PROFILE_HELP)

    web_p = sub.add_parser("web", help="start the local browser dashboard (drag & drop, no terminal needed after start)")
    web_p.add_argument("--host", default="127.0.0.1"); web_p.add_argument("--port", type=int, default=8765)
    web_p.add_argument("--token", default=None, help="shared token (required if --host is not loopback)")
    web_p.add_argument("--work-dir", default=None); web_p.add_argument("--no-browser", action="store_true")

    th_p = sub.add_parser("threads", help="map a hex colour to the nearest loaded thread(s) using CIEDE2000")
    th_p.add_argument("hex"); th_p.add_argument("--palette-csv", default=None); th_p.add_argument("--brand", default=None)

    emb_p = sub.add_parser("inspect-emb", help="report what the EMB adapter can and cannot do for a file (no parsing is faked)")
    emb_p.add_argument("path")

    from .cli_v06 import add_parsers as _add_v06
    _add_v06(sub)
    return p


def _print_fabrics() -> None:
    for fp in list_fabrics():
        print(f"{fp.name:<10} {fp.description}")


# ----------------------------------------------------------------------
# analyze / qc
# ----------------------------------------------------------------------


def _resolve_report_paths(args, stem: str) -> tuple[str, str | None]:
    out_dir = args.output
    html_path = args.html
    json_path = None

    if html_path is None:
        base = f"{stem}_report.html"
        html_path = os.path.join(out_dir, os.path.basename(base)) if out_dir else base

    if args.json:
        if isinstance(args.json, str):
            json_path = args.json
        else:
            base = f"{stem}_analysis.json"
            json_path = os.path.join(out_dir, os.path.basename(base)) if out_dir else base

    return html_path, json_path


def _cmd_analyze(args) -> int:
    if not args.file:
        print("ERROR: no .dst file given. Usage: embcopilot analyze design.dst", file=sys.stderr)
        return 1

    if not os.path.exists(args.file):
        print(f"ERROR: No such file: {args.file}", file=sys.stderr)
        return 1

    if not args.file.lower().endswith(".dst"):
        print(
            f"WARNING: '{args.file}' does not have a .dst extension \u2014 "
            "attempting to read it as Tajima DST anyway.",
            file=sys.stderr,
        )

    try:
        cfg = get_fabric_preset(args.fabric).config.override(spm=args.spm)
        design = read_dst(args.file)
        a = analyze(design, cfg)
    except InvalidDSTError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Please verify that the file is a valid, unmodified Tajima .dst embroidery file.", file=sys.stderr)
        if args.debug:
            raise
        return 2
    except CorruptDSTError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 3
    except DSTError as exc:  # pragma: no cover - defensive catch-all
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 4
    except OSError as exc:
        print(f"ERROR: Could not read '{args.file}': {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 5

    stem = os.path.splitext(args.file)[0]
    html_path, json_path = _resolve_report_paths(args, stem)

    try:
        render_html(a, design, html_path)
        if json_path:
            render_json(a, json_path)
    except OSError as exc:
        print(f"ERROR: Could not write report: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 6

    if a.stitches == 0:
        print("WARNING: this design contains zero sewing stitches.", file=sys.stderr)

    if not args.quiet:
        print(f"{a.name}  \u2014  fabric preset: {args.fabric}")
        print(
            f"  {a.stitches:,} stitches \u00b7 {a.colors} colours \u00b7 "
            f"{len(a.segments)} reconstructed segments \u00b7 "
            f"{a.width_mm:.1f} x {a.height_mm:.1f} mm"
        )
        print(
            f"  {a.estimated_trims} estimated trims \u00b7 {a.jumps} jumps \u00b7 "
            f"{a.runtime_minutes:.1f} min estimated run time \u00b7 "
            f"{a.thread_metres:.1f} m estimated thread"
        )
        print(f"  QC heuristic score: {a.score}/100 \u2014 {a.grade}")
        for i in a.issues:
            tail = f"  [recovers {i.saving}]" if i.saving else ""
            print(f"    [{i.severity:<8}] {i.title}{tail}")
            if args.verbose:
                print(f"        {i.detail}")
                if i.why_it_matters:
                    print(f"        Why it matters: {i.why_it_matters}")
                if i.recommendation:
                    print(f"        Recommended review: {i.recommendation}")
        print(f"  HTML report: {html_path}")
        if json_path:
            print(f"  JSON report: {json_path}")

    return 0


# ----------------------------------------------------------------------
# blueprint / technical-plan / simulate  (Phase 2)
# ----------------------------------------------------------------------


def _cmd_blueprint(args) -> int:
    from .vision import LocalVisionProvider, NotImplementedCapability

    if not args.image:
        print("ERROR: no reference image given. Usage: embcopilot blueprint photo.png --target-width-mm 80 --target-height-mm 80", file=sys.stderr)
        return 1
    if not os.path.exists(args.image):
        print(f"ERROR: No such file: {args.image}", file=sys.stderr)
        return 1

    try:
        bp = LocalVisionProvider().analyze(args.image, (args.target_width_mm, args.target_height_mm))
    except (NotImplementedCapability, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 2

    if getattr(args, "debug_dir", None):
        from .vision import export_debug

        for path in export_debug(bp, args.debug_dir):
            print(f"  debug artifact: {path}")

    out_path = args.output or (os.path.splitext(args.image)[0] + "_blueprint.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(bp.to_dict(), fh, indent=2)

    print(
        f"{len(bp.objects)} region(s) detected in {args.image} "
        f"(target size {args.target_width_mm:.1f} x {args.target_height_mm:.1f} mm)"
    )
    if bp.background:
        print(f"  background: {bp.background}")
    for w in bp.warnings:
        print(f"  WARNING: {w}")
    for obj in bp.objects:
        conf = obj.confidence.get("detection")
        conf_pct = f"{conf.pct}%" if conf else "?"
        extra = ""
        if obj.width_stats_mm:
            extra += f" width_mm~{obj.width_stats_mm['median']:.2f}"
        if obj.holes_mm:
            extra += f" holes={len(obj.holes_mm)}"
        if obj.centerline_mm:
            extra += " centerline=yes"
        print(
            f"  {obj.name:<10} category={obj.category:<8} "
            f"bbox_mm=({obj.bbox_mm[0]:.1f},{obj.bbox_mm[1]:.1f})-"
            f"({obj.bbox_mm[2]:.1f},{obj.bbox_mm[3]:.1f}) confidence={conf_pct}{extra}"
        )
    print(f"Blueprint written to {out_path}")
    print(
        "NOTE: categories are 'unknown' by design \u2014 this is geometric region "
        "detection, not semantic recognition. See README 'Vision engine'."
    )
    return 0


def _cmd_technical_plan(args) -> int:
    from .technical import TechnicalBlueprintEngine
    from .vision import LocalVisionProvider, NotImplementedCapability

    if not args.image:
        print("ERROR: no reference image given.", file=sys.stderr)
        return 1
    if not os.path.exists(args.image):
        print(f"ERROR: No such file: {args.image}", file=sys.stderr)
        return 1

    try:
        bp = LocalVisionProvider().analyze(args.image, (args.target_width_mm, args.target_height_mm))
    except (NotImplementedCapability, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.debug:
            raise
        return 2

    try:
        tb, decisions = TechnicalBlueprintEngine().generate(bp, fabric=args.fabric, production_profile=args.profile)
    except ValueError as exc:  # unknown production profile
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out_path = args.output or (os.path.splitext(args.image)[0] + "_technical_plan.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(tb.to_dict(), fh, indent=2)

    print(f"Technical blueprint for {args.image} \u2014 fabric: {args.fabric}")
    for plan in tb.plans:
        print(
            f"  {plan.object_name:<10} stitch={plan.stitch_type:<8} "
            f"density={plan.density_mm}mm underlay={plan.underlay} "
            f"pull_comp={plan.pull_compensation_mm}mm "
            f"direction={plan.direction_deg} confidence={plan.overall_confidence.pct}%"
        )
        print(f"      reason: {plan.stitch_reason}")
    if tb.small_detail_flags:
        print(f"  {len(tb.small_detail_flags)} small-detail warning(s):")
        for flag in tb.small_detail_flags:
            print(f"    {flag.object_name}: {flag.recommendation}")
    print(f"Technical blueprint written to {out_path}")
    return 0


def _summarize(res, args_profile) -> dict:
    return {
        "profile": args_profile,
        "objects": [{"id": o.object_id, "name": o.name, "semantic": o.category, "semantic_confidence": o.confidence.get("semantic").value if "semantic" in o.confidence else None,
                     "stitch": (res.plan.object_status.get(o.object_id) or {}).get("strategy")} for o in res.objects],
        "stats": res.plan.stats(),
        "validation": res.validation.status,
        "findings": [f"{f.severity} {f.code}: {f.message}" for f in res.validation.findings],
        "comparison": {c.object_id: c.status for c in res.comparison},
        "comparison_global": res.comparison_global,
        "risk": {a.object_id: a.risk for a in res.assessments},
        "timings_s": res.timings,
    }


def _cmd_stitches(args) -> int:
    from .pipeline import run_pipeline

    if not args.image or not os.path.exists(args.image):
        print(f"ERROR: No such file: {args.image}", file=sys.stderr)
        return 1
    try:
        res = run_pipeline(args.image, (args.target_width_mm, args.target_height_mm), args.profile, args.fabric, args.out_dir)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if res.stopped:
        print(f"STOPPED: {res.stopped}", file=sys.stderr)
        return 2
    summ = _summarize(res, args.profile)
    if args.json:
        print(json.dumps(summ, indent=2))
    else:
        print(f"{len(res.objects)} object(s), profile={args.profile}, validation={res.validation.status}")
        for o in summ["objects"]:
            print(f"  {o['name']:<10} {o['semantic']:<12} stitch={o['stitch']}")
        print("  stats (computed from generated stitches; time/thread are ESTIMATES):", {k: v for k, v in summ["stats"].items() if k != "ESTIMATE_ASSUMPTIONS"})
        for f in summ["findings"]:
            print("  -", f)
        print(f"  artifacts written to {args.out_dir} ({len(res.artifacts)} files)")
        print("  NOTE: SIMULATION preview is a flat render, not a physical thread model. Nothing was sent to Wilcom.")
    return 0 if res.validation.status != "ERROR" else 3


def _cmd_demo(args) -> int:
    from .demo import make_demo

    final = make_demo(args.out_dir, args.profile)
    print(f"demo written to {args.out_dir}/  (validation={final['validation']['status']}, stitches={final['stats_computed_from_stitches']['stitch_count']})")
    print("  reports/final_report.html, exports/design.dst (+ .exp EXPERIMENTAL); PES/JEF/EMB are UNAVAILABLE and not written")
    return 0


def _cmd_inspect_emb(args) -> int:
    from .automation.emb_adapter import adapter_for

    if not os.path.exists(args.path):
        print(f"ERROR: No such file: {args.path}", file=sys.stderr)
        return 1
    ad = adapter_for(args.path)
    if ad is None:
        print("EMB_WORKFLOW_UNAVAILABLE: no adapter recognises this file type", file=sys.stderr)
        return 2
    caps = ad.capabilities()
    print(json.dumps({"detected": ad.detect(args.path), "capabilities": caps.to_dict() if hasattr(caps, "to_dict") else str(caps),
                      "metadata": ad.metadata(args.path)}, indent=2, default=str))
    print("Native EMB read/edit/authoring: UNAVAILABLE (needs a supported Wilcom workflow). Nothing is parsed or faked.")
    return 0


def _cmd_simulate(args) -> int:
    from .automation.executor import ActionExecutor
    from .automation.mock_adapter import MockWilcomAdapter
    from .automation.schema import ActionRequest, AutomationPolicy
    from .technical import TechnicalBlueprintEngine
    from .vision import LocalVisionProvider, NotImplementedCapability

    print("SIMULATION MODE \u2014 MockWilcomAdapter only. No real Wilcom installation is touched.\n")

    plan_steps: list[tuple[str, dict]] = []
    if args.image:
        if not os.path.exists(args.image):
            print(f"ERROR: No such file: {args.image}", file=sys.stderr)
            return 1
        from .pipeline import run_pipeline

        try:
            res = run_pipeline(args.image, (args.target_width_mm, args.target_height_mm), args.profile, args.fabric)
        except (NotImplementedCapability, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            if args.debug:
                raise
            return 2
        if res.stopped:
            print(f"STOPPED: {res.stopped}", file=sys.stderr)
            return 2
        for act in res.action_plan:  # real geometry from the object model
            if act.params.get("status") == "GEOMETRY_UNAVAILABLE":
                plan_steps.append(("create_geometry", {"target": act.target, "status": "GEOMETRY_UNAVAILABLE"}))
                continue
            plan_steps.append((act.action, {"target": act.target, **act.params}))
    else:
        print("ERROR: simulate needs a reference image (no placeholder geometry is ever substituted).", file=sys.stderr)
        return 1

    print("PLAN:")
    for i, (action, params) in enumerate(plan_steps, 1):
        print(f"  {i}. {action}({params})")

    if args.dry_run:
        print("\nDRY RUN: no action executed.")
        return 0

    print("\nEXECUTING against MockWilcomAdapter:")
    executor = ActionExecutor(MockWilcomAdapter(), AutomationPolicy(allow_unattended_up_to="medium_risk"))
    for action, params in plan_steps:
        if params.get("status") == "GEOMETRY_UNAVAILABLE":
            print(f"  [skipped ] {action}: GEOMETRY_UNAVAILABLE for {params.get('target')} (not simulated)")
            continue
        target = params.get("target")
        req = ActionRequest(
            action=action,
            target=target,
            params={k: v for k, v in params.items() if k != "target"},
            risk="low_risk" if action == "select_tool" else "medium_risk",
            requires_confirmation=False,
        )
        result = executor.run(req, approved=True)
        print(f"  [{result.status:<8}] {action}: {result.detail}")
        if result.status == "failed":
            break

    print(f"\n{len(executor.log)} action(s) logged.")
    return 0


# ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Backward compatibility: `embcopilot design.dst ...` (no subcommand)
    # is shorthand for `embcopilot analyze design.dst ...`.
    if argv and not argv[0].startswith("-") and argv[0] not in KNOWN_SUBCOMMANDS:
        argv = ["analyze"] + argv

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_fabrics:
        _print_fabrics()
        return 0

    command = getattr(args, "command", None)
    if command in ("analyze", "qc"):
        return _cmd_analyze(args)
    if command == "blueprint":
        return _cmd_blueprint(args)
    if command == "technical-plan":
        return _cmd_technical_plan(args)
    if command == "stitches":
        return _cmd_stitches(args)
    if command == "demo":
        return _cmd_demo(args)
    if command == "web":
        from .web.server import serve
        serve(args.host, args.port, args.work_dir, args.token, not args.no_browser)
        return 0
    if command == "threads":
        from .threads import ThreadDatabase, describe_delta_e, hex_to_rgb
        db = ThreadDatabase.generic()
        if args.palette_csv:
            db.load_csv(args.palette_csv, args.brand or "Custom")
        for de, t in db.candidates(hex_to_rgb(args.hex), args.brand, 3):
            print(f"{t.brand} {t.code} {t.name} {t.hex}  DeltaE2000={de}  ({describe_delta_e(de)})")
        return 0
    from .cli_v06 import V06_COMMANDS
    if command in V06_COMMANDS:
        from .cli_v06 import run as _run_v06
        return _run_v06(args)
    if command == "inspect-emb":
        return _cmd_inspect_emb(args)
    if command == "simulate":
        return _cmd_simulate(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        # e.g. `embcopilot --list-fabrics | head` closing the pipe early —
        # not a real error, just stdout going away mid-write.
        sys.stderr.close()
        raise SystemExit(0)
