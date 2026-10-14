# LIMITATIONS (truthful status, v0.3.0)

| Capability | Status |
|---|---|
| DST QC analysis | IMPLEMENTED |
| Background removal (alpha, solid, near-white, simple gradient) | IMPLEMENTED (synthetic tests only) |
| Uncertain (anti-aliased) mask | PARTIAL (band by colour distance) |
| Anti-alias / JPEG fringe handling | PARTIAL (palette from interior, background-blend removal, sliver absorption) |
| Holes / nested contours | IMPLEMENTED for outer + holes; `islands_mm` field exists but is NOT populated (islands are separate objects) |
| Width profile, skeleton, centerline | IMPLEMENTED (Zhang-Suen, distance transform); ring/loop shapes give a loop skeleton that the stitch decision does not yet treat as a satin ring |
| Curvature / sharp-corner preservation | PLANNED |
| Semantic classification (wing, leaf...) | PLANNED — every object is UNKNOWN |
| Hierarchy, layers, overlaps, dependencies | PLANNED |
| Occlusion handling | UNAVAILABLE — an object hidden under another (branch under leaves) is split into pieces |
| Stitch type decision | PARTIAL (width/area/elongation rules; uses measured width when Vision V2 supplied it) |
| Satin rails, fill generation, stitch paths, validation | PLANNED |
| Production profiles | PARTIAL (real effect; values unvalidated) |
| Connector engine | PARTIAL (distance cost model; profile weights; not geometry-aware) |
| Sequence engine | PARTIAL (dependencies + stage order; no travel optimisation over real geometry) |
| Reference-vs-design comparison | PARTIAL (counts + bbox only) |
| Mock Wilcom adapter | MOCK |
| Wilcom ES-65 / E4 automation | UNAVAILABLE / PLANNED |
| .EMB read/write | UNAVAILABLE (detect + raw byte metadata only) |
| GUI | PLANNED |
| Analysis resolution | Images are analysed at <= 768 px on the long side; thinner features can vanish |
| Non-uniform target aspect | Widths use the geometric-mean scale (approximate) |
| Pixel thresholds | `bg_tolerance=40`, `min_area_px=40`, `min_hole_area_px=12` are defaults, tuned only on synthetic art |

## Added in v0.4.0
- Semantic classes other than stem/branch/leaf/petal/text/eye(candidate)/decoration/small_detail/background cannot be inferred from geometry.
- Occlusion merge assumes a hidden object continues in a straight-ish line with matching width and colour.
- Width profile of elongated shapes is held constant over the last ~1.5 widths at each end (raster artefact correction), so real tapers are not represented.
- Satin: single column or concentric ring only; branched/holed/eccentric shapes fall back to fill.
- Fill is a tatami-style stagger, not Wilcom's fill algorithm; underlay parameters are unvalidated defaults.
- TRIM is exported to DST as three zero-length jumps (machine-dependent convention).
- Time/thread numbers are estimates from stated assumptions.
- No real embroidery artwork, machine or Wilcom installation was used anywhere.

## Added in v0.5.0
- Jump count is high on concave fills (flower demo: 165 jumps); needs region decomposition (PLANNED).
- Semantic classes are guesses; the demo mislabels a flower as `unknown` and small details.
- Puff/appliqué/cross/gradient parameters are unvalidated; no foam/fabric behaviour is modelled.
- EXP verified against this project's reader only; PES/JEF/EMB unavailable; DST trim/stop encodings are machine-specific.
- SAM 2 code path never executed (no torch/sam2/GPU here). Dashboard never opened in a real browser by the author.
- Thread data: generic palette only. Fabric numbers: heuristic.

## Added in v0.6.0
- Tatami "mesh look" and concave-fill intra-object jumps are NOT fixed (vector badge: 193 jumps, raster badge: 174); the travel policy classes them as hidden/in-region.
- The raster pipeline does not yet auto-replace detected circular text with CurvedTextEngine glyphs (no OCR). Use `embcopilot badge` / `vectorbadge` for the vector-first path.
- Raster badge golden still has 8 protected-zone crossings and 1 QC ERROR (CENTER_CROSSING_TRAVEL); export is correctly BLOCKED_BY_QC unless an expert override with a reason is logged.
- Trims increase (5→67 raster badge, 6→82 vector badge): fewer dragged threads, more trim cycles. Not validated on a machine.
- Font suitability, trim, QC, machine-profile numbers are heuristic defaults, not sew-out calibrated.
- Protected zones need ≥10 mm clear width: small empty centres are not protected.
- Command parser understands a fixed English/Hinglish vocabulary; it never executes and does not apply steps to the design yet.
- No React/FastAPI, canvas editor, object inspector, thread-texture render, OCR, PES/JEF/VP3, real Wilcom, SAM2 run, or sew-out validation.

## Added in v0.7.0
- Cloud LLM paths are untested against live services (no network/keys here); default model names are unverified.
- Prompt-box width edits work only for concentric circular rings; other objects get `WIDTH_NOT_APPLIED` with a reason.
- Rule parser understands a fixed vocabulary; ambiguous requests are listed as "not understood", not guessed.
- "Reduce trims" is a preference (longer hidden detours), not a guarantee; measure with the before/after preview.
- EMB: inspection is file-level only; creation/editing needs a Wilcom backend that this build does not implement.
- No Next.js/React build, no WebGL canvas, no streaming progress.
