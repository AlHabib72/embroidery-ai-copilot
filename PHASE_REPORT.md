# PHASE_REPORT — Phase A (Repair) + Phase B (Vision V2, core)

## Implemented
- **A**: audit; spec-correct, validated confidence thresholds; `profiles.py`
  (11 profiles) wired into density, underlay tier, pull compensation,
  min-feature flags and connector weights; `--profile` CLI flag (unknown profile
  = error, exit 2); measured width/area used by the technical engine.
- **B (partial)**: background model + masks, normalisation, small island/hole
  filtering, palette-from-interior + blend removal + sliver absorption,
  contour hierarchy (holes), width statistics, skeleton/centerline with
  branch/end counts, bounded-resolution analysis, numbered debug export
  (`blueprint --debug-dir`: files 01-06).

## Changed
`domain.py` (thresholds, new optional geometry fields), `vision.py` (rewritten
LocalVisionProvider; public API unchanged), `knowledge.py`, `technical.py`,
`connector.py`, `cli.py`, README, pyproject 0.3.0.

## Tests
179 original (unchanged, all pass) + 30 new behavioural tests
(`test_vision_v2.py`, `test_profiles.py`) = **209 pass, 0 fail**.
Run locally with a minimal pytest stand-in; please re-run with real pytest.

## Known limitations / remaining risks
See LIMITATIONS.md. Not validated on real embroidery artwork. Occluded
objects split; rings not recognised as satin loops; semantics absent.

## NOT done in this pass (still open from the master prompt)
Phase B semantics + curvature; Phases C–K entirely. Quality gate says do not
proceed until Phase B is reviewed on real artwork.

## Next phase
Finish B (semantic classifier v1, curvature), then C (hierarchy, overlaps,
occlusion merge) using your real reference designs as fixtures.
