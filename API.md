# API (stdlib server; typed dataclass responses as JSON)
`GET /api/capabilities` · `GET /api/options` · `POST /api/jobs` (raw body + `X-Filename`) · `GET|DELETE /api/jobs/{id}` · `POST /api/jobs/{id}/analyze` {width_mm,height_mm,fabric,profile,brand,colorway_mode,hoop,segmentation,vector_fit,overrides,targets} · `/cancel` · `/repair` {apply:[names]} · `/approve` {approved,note} · `/export/{dst|exp|pes|jef|emb}` (403 until approved; 501 for unavailable; 422 if validation has errors or read-back fails) · `/segment` (501 unless SAM 2 configured) · `/command` (parse only) · `GET /zip` · `GET /artifacts/{path}`.
Errors: `{"error": CODE, "message": ...}`. Limits: 50 MB upload, 240 req/min/IP, traversal blocked, loopback only unless `EMBCOPILOT_TOKEN`/`--token`.
The task-spec endpoints `/analyze /segment /blueprint /technical /stitches /qc /repair /compare` are folded into the job lifecycle (one pipeline run produces all); per-stage endpoints are PLANNED.

## v0.6 additions
GET /api/fonts?limit= · GET /api/fabrics · GET /api/threads · GET /api/machines · GET /api/emb/status · GET /api/v06/capabilities
POST /api/commands {"text": "...", "detected_roles": [...]} → validated multi-step plan (executed:false)
POST /api/jobs/{id}/qc2 {"machine": "generic_flat", "expert_override": {"confirm": true, "reason": "...", "user": "..."}} → production pass + QC v2 + QC-gated export package
POST /api/jobs/{id}/emb/export → 409 with the "Native EMB Export Unavailable" message unless a verified backend exists
Brief endpoints not implemented as separate routes (the v0.5 job API covers them): /vision, /segment (SAM2 reports unavailable), /blueprint, /plan, /generate, /repair, /approve, /export.
