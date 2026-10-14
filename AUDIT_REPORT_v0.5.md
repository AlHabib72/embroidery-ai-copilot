# AUDIT REPORT v0.5 (input: v0.4.0)

**Baseline:** 260 passed / 0 failed / 0 skipped (22 s), Python 3.12; pytest/ruff/mypy unavailable offline (stand-in runner used; please re-run with real pytest). Coverage not measured.
**Now:** 368 passed / 0 failed / 0 skipped.

## v0.4.0 problems named in the brief — verified
| # | Problem | Verified | Now |
|---|---|---|---|
| 3.1 | Semantics heuristic | yes | still heuristic (PARTIAL), ring/glyph confusion fixed; honest labelling; model slot (SAM2 masks) added |
| 3.2 | Geometric, not semantic hierarchy | yes | unchanged (PLANNED) |
| 3.3 | Occlusion over-confident | partly (confidence <0.96, flagged) | unchanged; kept heuristic + review flag |
| 3.4 | Straight-line connectors | yes | hidden A* routing around open fabric + edge walk inside objects (PARTIAL) |
| 3.5 | Hard vs soft sequence rules | dependencies were already hard | documented + tested (random-DAG property test) |
| 3.6 | Basic comparison | yes | + contour chamfer, hole topology |
| 3.7 | No repair loop | yes | propose/simulate/re-QC/approve loop |

## Bugs found by running/rendering during this pass (each has a test)
1. Export ignored validation errors (demo wrote a DST with a missing object) → export now refuses on ERROR.
2. A failing user override (puff on a 1.5 mm sliver) dropped the object → now falls back visibly (`OVERRIDE_FAILED_FELL_BACK`).
3. Puff/appliqué border ignored holes (stitched across the ring's hole) → one band per boundary.
4. Stitch joins could bridge thin holes (sampling too coarse) → sampled every ≤0.4 mm.
5. Edge-travel corner cutting (resampling dropped polyline corners) → corners preserved; strict polygon re-check.
6. Ring classified as TEXT and glyph as DECORATION → closed-loop aware.
7. Vector SVG was silently rescaled to the requested size → native size kept unless `vector_fit`.
8. PDF stream regex ate a trailing EOL byte, breaking AI files → tolerant decompression.
9. Round-trip compare was origin-dependent → shape-only comparison.
10. Memory guard assumed 4 GB for every model → per-model `need_gb`.

## Still open (honest)
Semantic hierarchy; real artwork; sew-outs; Wilcom; native EMB; PES/JEF; SAM 2 verification; concave-fill jump count (demo: 165 jumps; needs region decomposition); React frontend; learning.
