# AUDIT REPORT v0.6 (input: v0.5.0)

Method: extracted the v0.5.0 zip, read the module list, capability matrix, limitations, audit v0.5, CLI, pipeline, stitch engine, strategies,
formats, EMB adapter, web server; ran the v0.5 suite (stand-in runner `tools/mini_pytest.py`, real pytest not installable offline here).

**Baseline:** 368 passed / 0 failed. **Now:** see TESTING.md (435 passed incl. 67 new v0.6 tests).
Not read line-by-line: `vision.py` (33 KB), `semantics.py`, `knowledge.py` internals - they were exercised through the pipeline and tests only.
No real artwork, sew-out, Wilcom, third-party reader, GPU or SAM 2 checkpoint was available. Everything below is measured on SYNTHETIC art.

## WHAT EXISTS (v0.5)
Vision V2 (background/holes/centerline), SVG/AI input, object blueprint + semantics (heuristic), technical blueprint, stitch engines (run, satin
column/ring, tatami, appliqué, puff, cross, gradient), A*-grid hidden routing, sequence engine, validator (15 checks), DST/EXP export + read-back,
fabric profiles, thread palettes + CIEDE2000, repair loop, stdlib web dashboard, mock Wilcom adapter, EMB *interface* (always unavailable).

## WHAT WORKS / PARTIAL / BROKEN / MOCK / MISSING (relative to the v0.6 brief)
| Brief item | v0.5 state | v0.6 state |
|---|---|---|
| 3 Lettering from true vector glyphs | MISSING (text = raster blobs) | IMPLEMENTED `fonts.py` (TTF/OTF/TTC, kerning, counters) |
| 2.2/2.4/2.5 Circular text, spacing, orientation | MISSING | IMPLEMENTED `curvedtext.py` (arc-length, 6 modes, auto-flip) - visually verified |
| 2.10 Rough circular borders | raster contour noise | IMPLEMENTED `circlefit.py` parametric circle + `satin_ring`; raster path unchanged unless a circle is inferred |
| 2.6 Stitch angle oscillation | not measured | IMPLEMENTED measured + prevented in `satin_v2.py` (unit-vector smoothing, rate limit); oscillation counter tested |
| 2.7/2.8/2.9 Centre-crossing travel, too many trims, short stitches | present (demo: 73 protected crossings on badge golden) | PARTIAL: 73 -> 8 on raster badge golden, 24 -> 0 on multi-object golden, 25 -> 0 on vector badge; short-stitch 19 -> 11 |
| 2.11 Mesh-like tatami | NOT FIXED | NOT FIXED (v0.5 tatami generator unchanged; concave-fill jumps remain, now classified hidden when intra-object) |
| 2.12 Missing underlay | existing underlay | unchanged + QC rule MISSING_UNDERLAY |
| 2.13/2.14 Hierarchy / negative space | geometric hierarchy only | PARTIAL: `negative_space.py` (NegativeSpace, ProtectedTravelZone), `badge.py` CircularBadgePlanner; semantic hierarchy still heuristic |
| 2.17 Preview != stitch plan | already same plan | IMPLEMENTED `plan_to_svg`/`render_png` derive from StitchPlan; test enforces |
| 2.18 Export success not validated | read-back via own parser | IMPLEMENTED + extents/END/count validation in `exportpkg.validate_exported`; QC-gated |
| 2.19 Native EMB failing | interface only | IMPLEMENTED HONESTLY: `nativeemb.py` backends + state machine + verification contract; **still REQUIRES_WILCOM_CONFIGURATION** |
| 2.20 GUI = tech demo | tabs + approval gate | PARTIAL: added v0.6 panel (command plan, QC v2, EMB button state). Not a full canvas editor |
| Machine/hoop profiles, trim policy | none | IMPLEMENTED (generic presets) |
| QC v2 categories, release gates, export blocking, override log | 15 checks | IMPLEMENTED |
| Project store, undo/redo, versions, resume | none | IMPLEMENTED (`.eacproject` directory) |
| Learning (opt-in), sew-out record, calibration | schema only | IMPLEMENTED (opt-in, exportable, deletable) |
| Multi-step commands (EN + Hinglish) | single keyword parser | PARTIAL: parse + validate only; NOT wired to execute |
| Design diff OLD/NEW | none | IMPLEMENTED |
| PES / JEF / VP3 | UNAVAILABLE | UNAVAILABLE (needs vendor colour tables; guessed files rejected) |
| SAM2 | EXPERIMENTAL | unchanged: never exercised here |
| React/Next.js + FastAPI | stdlib server + static page | PLANNED (FastAPI/React not installable offline here; same job manager) |
| Interactive canvas editor, object inspector, playback v2 | playback.html | PLANNED (existing playback unchanged) |
| Thread brand data | generic palette | REQUIRES_LICENSED_RESOURCE (import CSV) |

## WHY THE v0.5 DEMO LOOKED BAD (root causes found by measuring)
1. Text and rings came from raster contours: glyph shapes noisy, circle edges jagged, no notion of baseline/arc.
2. Concave regions were stitched as ONE tatami object with long intra-object jumps (vector badge: 550 of 575 jumps; 63-88 mm each, across the ring hole).
3. Travel between objects was unconstrained by empty regions; 73 thread-bearing moves crossed the empty centre on the raster badge golden.
4. Rings were tatami fills unless Vision supplied `closed_loop`; vector rings had none => fell back silently to tatami.
5. QC counted jumps, not *thread-bearing travel*; a trimmed jump (no thread dragged) looked as bad as a dragged one.

## HOW EACH WAS ADDRESSED / WHAT IS STILL OPEN
Addressed: 1 (vector path), 3, 4 (vector rings carry closed_loop + exact width), 5 (QC/crossing logic distinguishes trimmed travel).
Open: 2 for tatami glyph fills (jump count did not fall: 193 on vector badge, 174 on raster badge; they are now classed *hidden* and not trimmed, but still exist);
raster badge keeps 8 protected crossings and an `EXCESSIVE_JUMP` warning; trims rise (5 -> 67 / 6 -> 82) because the policy trims thread-bearing visible travel -
this is a TRADE-OFF (fewer dragged threads, more trims), reported in every diff. Whether that is right for your machine needs a sew-out.

## BUGS FOUND AND FIXED WHILE BUILDING v0.6 (each has a test)
1. Protected-zone builder treated a ring as covering its own hole -> no zone. Now per-object (outer - holes).
2. Narrow gaps between letters became "protected" -> thousands of false crossings. Zones now need >= 10 mm clear width (morphological opening).
3. Closed-loop satin angle smoothing on the unwrapped angle broke at the seam. Now smooths unit vectors.
4. Hull-based rerouting could not escape annular zones. Replaced by grid A* with exact masks.
5. Sub-mm grazes counted as crossings. Tolerance 1 mm (configurable).
6. Short-stitch "corner" threshold was exercised by a wrong test; fixed test, behaviour documented (>=60 deg turn).
7. Dashboard JS parse test broke when a second `<script>` block was added; merged into one block.
