# AUDIT REPORT — Embroidery AI Copilot (input: phase-2 zip, v0.2.0)

Audit date: 2026-09-29. Method: read every module, ran the suite, ran CLI
smoke tests, ran the vision pipeline against synthetic images with known
ground truth (script results quoted below are real output).

## Current architecture
Flat package `src/embcopilot/` (~6,100 lines incl. tests): Phase-1 DST QC
(`dst, analyze, metrics, rules, config, report, models`), Phase-2 advisory
layer (`domain, vision, knowledge, technical, command, connector, sequence,
comparison, session, audit`) and `automation/` (schema, executor, mock
adapter, honest Wilcom ES-65 placeholder, DST/EMB file adapter).

## Working components (verified)
- DST parser/QC analyzer; CLI `analyze`; HTML/JSON report — 179 original tests pass.
- Command parser (regex, anti-hallucination), session/audit JSON, executor with
  emergency stop, mock Wilcom adapter, sequence topological sort with cycle error.
- Honest placeholders: `WilcomES65Adapter` and `CloudVisionProvider` raise
  `NotImplementedCapability` (no faking).

## Broken components (found by running them)
| # | Finding | Evidence |
|---|---|---|
| B1 | **Background reported as an object** with 95% confidence: white, coloured, and transparent PNG (alpha dropped -> black rectangle). | `ring.png` -> 3 regions, Region 1 = whole canvas rgb(254,254,254); `alpha.png` -> Region 1 = rgb(0,0,0) full canvas |
| B2 | **Holes destroyed** (`RETR_EXTERNAL`): a ring is one filled blob, area/bbox of the filled disc. | ring detected as solid |
| B3 | **Anti-alias / JPEG fringes** become extra palette colours -> fake objects. | reproduced by test at q=30 JPEG: 8 objects for 1 circle |
| B4 | **Width = min(bbox w,h)**: a thin diagonal stem (65x65 mm bbox) was "wide" -> Tatami. | `estimate_width_mm` docstring admits it |
| B5 | **`production_profile` accepted, stored, never used.** No CLI flag either. | grep: only pass-through in `technical.py` |
| B6 | Confidence thresholds `auto_safe=0.90` contradict the spec (0.95) and were not validated (`require_review > auto_safe` was accepted). | `domain.ConfidenceThresholds` |
| B7 | Connector weights had no profile link ("quality first would…" was only a comment). | `connector.py` docstring |
| B8 | Object count depended on export resolution (same art at 4x size -> 9 objects instead of 7) because pixel thresholds are resolution-bound; 1600px took 12 s. | measured, then fixed |

## Weak tests (pass without proving behaviour)
`test_vision.py::detects_distinct_regions` asserted only `len(objects) >= 2` —
it passed while the background counted as an object (B1). Superseded by
`test_vision_v2.py` (ground-truth geometry assertions).

## Partial components
Comparison (object-count + bbox size only), EMB adapter (detect + byte metadata,
no parsing), connector (gap-distance cost model; no geometry), sequence (stage
ranks + dependencies; no geometric dependencies), technical (bbox-free only
where Vision V2 supplies measurements).

## Missing components (vs the master prompt)
Semantic classifier, hierarchy/parent-child, overlap/layer relations, satin
rails, fill generation, stitch-path engine + validation, stitch-angle field,
underlay as components, pull-comp model object, real geometry connectors,
real geometric comparison, screen-state model, UI automation, ES-65/E4
adapters, `.ecp` format, GUI, learning engine, property tests, real-design
tests, structured JSON logs.

## Incorrect / stale claims
- README said vision region "confidence" rewards size/smoothness — true, but the
  background got 95%: the claim "Supported" was overstated (fixed in README).
- Threshold documentation (0.90) contradicted the required 0.95 (fixed).

## Test results
Before: 179 passed / 0 failed. After Phase A+B: 209 passed / 0 failed.
Lint / type check: none configured in the project; `ruff`, `mypy`, `pytest`
could not be installed in the audit sandbox (no network). `compileall` clean.
Tests were executed with a minimal local pytest stand-in (supports only
`raises`, stacked `parametrize`, `approx`) — please re-run with real pytest.

## Risk areas
Colour-only object separation (occluded objects split into pieces — see
composite test: a branch under two leaves became 3 objects); ring/annulus
shapes are not recognised as satin loops; profile numbers unvalidated;
Wilcom automation entirely unimplemented.

## Recommended migration sequence
A Repair (done) -> B Vision V2 (core done, semantics pending) -> C Object
Blueprint hierarchy/overlaps -> D Technical Blueprint (rails, fills, angles)
-> E Stitch Path -> F Connector/Sequence V2 -> G Comparison -> H Automation
-> I EMB workflow -> J GUI -> K real-world validation.


---
## Addendum (v0.4.0) — the 14 issues named in the second brief, verified against the code

| # | Issue | Verified state at v0.3.0 | Now |
|---|---|---|---|
| 1 | pyproject vs `__init__` version | MISMATCH (0.3.0 vs 0.2.0) — my own v0.3.0 bump only touched pyproject | single source, regression test |
| 2 | CLI version output | printed the stale `__init__` value | fixed, tested |
| 3 | `simulate` dummy geometry | CONFIRMED (`[(0,0),(1,1),(2,0)]` for every object) | real geometry; `GEOMETRY_UNAVAILABLE` if none; refuses without image |
| 4 | weak confidence test | CONFIRMED (`len(set(...)) >= 1`) | replaced with `abs(a-b) > 0.05` on differing evidence |
| 5 | semantic classification | missing | PARTIAL, heuristic |
| 6 | ring/loop regression | thin | ring, donut, glyph counter, satin ring tests |
| 7 | multi-colour | thin | floral/badge/logo pipeline tests |
| 8 | touching objects | none | test added |
| 9 | occlusion | none | branch-under-leaves chain test + sewing-order regression |
| 10 | real embroidery references | none | **STILL NOT DONE** — no real references were available |
| 11 | geometric comparison | count/bbox only | coverage/spill/centroid/size (PARTIAL) |
| 12 | stitch geometry | absent | implemented (see matrix) |
| 13 | EMB authoring | absent | UNAVAILABLE by design |
| 14 | Wilcom automation | mock only | still MOCK / UNAVAILABLE |
