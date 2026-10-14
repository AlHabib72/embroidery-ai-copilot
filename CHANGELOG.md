# CHANGELOG

## 0.7.0
Added: `ai/` (schemas, local_parser, providers, router, policy, session, controller, audit), `emb_studio`, CLI `ask|emb-info|emb-handoff`, API `/api/ai/*`, `/api/emb/capabilities|inspect`,
`/api/jobs/{id}/ai/*`, `/api/jobs/{id}/emb/handoff`, dashboard Prompt Box, `web-next/src/lib` (typed client, `tsc --strict` clean), `.env.example`, `.gitignore`, SECURITY.md.
Fixed: satin rings ignored the requested underlay; added `prefer_fewer_trims` to travel optimiser.
Tests: +53 (AI layer, session/controller, EMB Studio, web/CLI). Docs: AUDIT_REPORT_NEXT_VERSION, CAPABILITY_MATRIX_v0.7, AI_PROMPT_BOX, EMB_INTEGRATION, FORMAT_CAPABILITIES.
Not done: Next.js/React studio, WebGL canvas, live LLM verification, SAM2 run, PES/JEF/VP3, real Wilcom backend, sew-out validation.

## 0.6.0
Added: `fonts` (vector glyphs, kerning, suitability), `curvedtext` (true text-on-circle), `circlefit`, `badge` (CircularBadgePlanner),
`satin_v2` (Input C), `negative_space` (+ protected zones, routing), `machine` (profiles, trim policy), `travel`, `stitchclean`, `qc_v2`
(gates, export blocking), `designdiff`, `projectstore`, `learning`, `command_v2` (EN/Hinglish), `exportpkg`, `production`, `nativeemb`, `vectorbadge`, `golden`,
CLI: capabilities, benchmark, fonts, badge, command, export-all, text-check. Web: /api/fonts, /fabrics, /threads, /machines, /commands, /emb/status, /jobs/{id}/qc2, /jobs/{id}/emb/export.
Dashboard: v0.6 panel. Tests: +67 (435 total). Docs: AUDIT_REPORT_v0.6, CAPABILITY_MATRIX_v0.6, EMB, WILCOM, TESTING, INSTALL, etc.
Known open: tatami mesh look, concave-fill jumps, OCR, PES/JEF/VP3, canvas editor, React/FastAPI, SAM2 execution, real Wilcom, sew-out validation.
Honesty: native EMB is never faked; QC ERROR blocks export; wording is "production-oriented / requires sew-out validation".

# CHANGELOG

## 0.5.0
- Inputs: SVG (exact vector geometry), PDF-compatible AI, content sniffing; native size kept for vectors.
- Stitch types: appliqué, puff, cross stitch, banded gradient, STOP command; user overrides with visible fallback.
- Threads (CIEDE2000, CSV palettes, colorways), 11 fabric profiles + push-pull model, hoop check.
- Connector: hidden A* routing, edge-walk travel inside objects. QC rules + propose/simulate/re-QC/approve repair loop.
- Comparison: contour chamfer + hole topology. Confidence/risk unchanged.
- Formats: EXP (experimental) with read-back verification; PES/JEF/EMB explicitly UNAVAILABLE; export gated on validation.
- Segmentation provider interface, mask clean-up, ModelManager, SAM 2 slot (experimental, falls back).
- Web dashboard + job system + approval gate; `embcopilot web|threads|demo`; canonical design model; EMB adapter interface.
- 10 bugs found by running/rendering fixed (see AUDIT_REPORT_v0.5.md).

## 0.4.0
- P0: single version source (`embcopilot.__version__`; pyproject reads it); CLI reports it. `simulate` now builds its plan
  from real object geometry (was a dummy triangle) and refuses to run without an image. Confidence-test made meaningful.
- New: `semantics.py` (heuristic classifier with alternatives/reasons), `blueprint.py` (containment hierarchy, touch/overlap,
  occlusion-continuation merge, z-order, dependencies), `geometry.py`, `strategies.py` (run, satin column, satin ring, tatami
  fill, underlay), `stitch.py` (StitchPlan, assembler, validator, DST export), `connector.route_connector`,
  `sequence.order_objects`, `confidence.py`, `simulate.py`, `compare_geo.py`, `pipeline.py`, `report_html.py`.
- New CLI: `stitches`, `demo`, `inspect-emb`; `--profile` on technical-plan/simulate/stitches.
- Bugs found by rendering and fixed (each has a regression test): fill joins bridged holes (A counter); merged branch sewn over
  the leaves hiding it; spurious skeleton branch points; resolution-dependent results; DST started with a phantom travel from origin.
## 0.3.0
- Vision V2, production profiles, spec-correct confidence thresholds (see PHASE_REPORT.md).
