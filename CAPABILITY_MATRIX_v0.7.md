# CAPABILITY MATRIX - v0.7.0
(Everything tested on synthetic art + fake network transports. No live LLM, Wilcom, browser, GPU or sew-out was available.)

## Prompt Box / AI
| Feature | State | Notes |
|---|---|---|
| Structured intent schema (16 ops, ranges, enums) | IMPLEMENTED | unknown ops/params/out-of-range values rejected and reported |
| Offline rule parser (English + Hinglish) | IMPLEMENTED | handles both spec example prompts; fixed vocabulary |
| Groq / OpenRouter / Gemini text parsing | EXPERIMENTAL (REQUIRES_CONFIGURATION) | fake-transport tested only; keys via env; models unverified |
| Router: LOCAL / CLOUD / HYBRID, retry, timeout, recorded fallback | IMPLEMENTED | CLOUD never silently uses local; HYBRID says so |
| Cloud privacy: only prompt text + role names sent | IMPLEMENTED | `privacy_manifest()` shown in UI |
| Gemini visual QC (advisory) | EXPERIMENTAL | needs explicit per-call consent + key; output sanitised, never becomes coordinates |
| Policy: risk, SUGGEST/CONFIRM/AUTO/MANUAL | IMPLEMENTED | decided by code, not by the model |
| Preview before/after, editable & droppable chips, approve/reject | IMPLEMENTED | edited values re-validated server-side |
| Apply to engine (stitch type, ring width, density, pull comp, underlay, angle, fabric, colour, delete, auto-split, min stitch, travel/fewer trims, protect) | IMPLEMENTED | real rebuild: technical blueprint -> stitch plan -> travel -> cleanup -> QC v2 |
| Width change | PARTIAL | exact only for concentric circular rings; otherwise refused with a reason |
| Undo / redo | IMPLEMENTED | state snapshots |
| Audit log (JSONL, redacted) | IMPLEMENTED | command id, prompt, provider/model, risk, approval, before/after metrics |
| API-key safety (.env.example, gitignore, redaction, no key via API) | IMPLEMENTED | |
| Streaming / WebSocket / SSE progress | PLANNED | polling only |
| Next.js/React PromptBox.tsx, StitchCanvas.tsx | PLANNED | typed client layer (`web-next/src/lib`) passes `tsc --strict`; no React build |
| 3D thread view / WebGL canvas / heatmap UI | PLANNED | |

## EMB
| Capability | State |
|---|---|
| EMB_INSPECTION | PARTIAL (size, SHA-256, signature, entropy, undecoded strings; rejects renamed DST/PES) |
| EMB_IMPORT | UNAVAILABLE (export DST/PES from Wilcom instead) |
| EMB_EDIT / EMB_CREATE / EMB_SAVE / EMB_EXPORT | REQUIRES_WILCOM_CONFIGURATION (configured env => CONFIGURED_PROTOCOL_NOT_IMPLEMENTED) |
| Handoff bundle (verified DST + per-object SVG with holes + parameter manifest CSV/JSON + README) | IMPLEMENTED - **not an EMB** |
| Wilcom backends (API/SDK/desktop) | REQUIRES_WILCOM_CONFIGURATION; protocol NOT implemented |
| Fake/renamed EMB output | never produced (tested) |

## Carried over from v0.6 (unchanged): DST/EXP verified export; PES/JEF/VP3 UNAVAILABLE; QC v2 + gates; vector glyph text + curved text; SAM2 EXPERIMENTAL/unrun; physical sew-out simulation UNAVAILABLE.

## Measured (`benchmark/benchmark.json`, CPU sandbox) v0.5-pipeline -> v0.7 production pass
| design | s | stitches | jumps | trims | protected crossings | QC errors |
|---|---|---|---|---|---|---|
| 01_circle | 1.1 | 3958 -> 3958 | 1 -> 1 | 1 -> 1 | 0 -> 0 | 0 -> 0 |
| 02_ring | 0.6 | 1602 -> 1602 | 1 -> 1 | 1 -> 1 | 0 -> 0 | 0 -> 0 |
| 11_complex_badge (raster) | 3.7 | 4210 -> 4154 | 169 -> 174 | 5 -> 67 | 73 -> 8 | 1 -> 1 |
| 12_multi_object_logo | 1.5 | 2517 -> 2517 | 33 -> 33 | 3 -> 33 | 24 -> 0 | 1 -> 0 |
| vector badge (CLI `badge`) | ~5 | 5440 -> 5432 | 193 -> 193 | 6 -> 82 | 25 -> 0 | 0 -> 0 |
Trims go up on purpose (thread no longer dragged across protected areas). Jumps do not fall. The raster badge still has 8 crossings and 1 QC ERROR (export correctly blocked).
Prompt-box example: ring 6 -> 2.5 mm satin + denim + zigzag underlay on the vector badge rebuilt in ~1 s; stitch count 5432 -> 5765.
