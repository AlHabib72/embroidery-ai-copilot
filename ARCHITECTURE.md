# ARCHITECTURE (v0.4.0)

Flat package `src/embcopilot/` (kept; the audit found no reason to move ~6k working lines into a deep tree).

```
image ─ vision.py (background, holes, widths, centerline)
      ─ blueprint.py (containment, overlap, occlusion merge, z-order, dependencies)
      ─ semantics.py (heuristic type + alternatives + reason)
      ─ technical.py + knowledge.py + profiles.py (stitch type, density, underlay, pull comp)
      ─ strategies.py (run / satin / satin ring / tatami / underlay -> points)
      ─ stitch.py (sequence.order_objects + connector.route_connector -> StitchPlan; validate; DST)
      ─ compare_geo.py / confidence.py / simulate.py / report_html.py
      ─ pipeline.py (orchestration, numbered artifacts 01–12, fail-safe stop)
      ─ automation/ (schema, executor, MockWilcom, honest ES-65 placeholder, file adapters) — plan only
```
Rules enforced in code: no stage runs after a failed stage (`VISION_FOUND_NO_OBJECTS`); an object whose geometry cannot be
generated is skipped and reported (`OBJECT_NOT_GENERATED`), never filled with guessed stitches; invalid satin falls back to fill
with a recorded `FALLBACK_SATIN_TO_TATAMI`; the action plan is never executed by the pipeline; the core imports nothing from Wilcom.

## v0.5 additions
inputs.py (sniff) · vector.py (SVG/AI) · segmentation.py (providers, masks, ModelManager) · threads.py · fabrics.py · strategies.py (+appliqué/puff/cross/gradient) ·
connector.route_hidden_path · repair.py · compare_geo (contour/topology) · formats.py (DST/EXP + verification) · emb.py (adapter interface) · design.py (canonical model) ·
web/ (jobs.py, server.py, static/index.html) · demo.py. The core never imports web/; web/ only calls pipeline/repair/formats.

## v0.6 layers
vision/blueprint (v0.5) → technical blueprint → stitch plan (v0.5) → **production pass** (zones → travel policy → short-stitch cleanup → QC v2 → diff → export package). Vector-first path: `vectorbadge` → same engines.
Canonical state is the StitchPlan + EmbroideryObject list; preview, QC, stats and exports all read it. Project history: `projectstore`.
