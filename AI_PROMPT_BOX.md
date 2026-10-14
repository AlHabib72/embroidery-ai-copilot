# AI Prompt Box (v0.7)
Flow: text -> router (LOCAL rules | Groq -> Gemini -> OpenRouter) -> JSON steps -> **schema validation** -> policy/risk -> preview (real before/after rebuild) -> your approval -> DesignSession -> QC -> audit.
The model can only choose from 16 whitelisted operations with range-checked numbers. It cannot run code, touch files, emit stitch coordinates, or skip approval.
Operations: set_stitch_type(+width), set_width, set_density, set_pull_comp (mm or %), set_underlay, set_angle, set_fabric, set_color, delete_object, auto_split, min_stitch, optimize(travel|fewer_trims|short_stitches), protect, export(dst|exp|pes|jef|emb), emb_info, qc.
Targets: object ids, roles (`outer_ring`, `inner_ring`, `text`, `monogram`, `background`), `type:satin`, `all`. Unknown targets are reported (`TARGET_NOT_FOUND`), never invented. `preserve X` protects X from later steps in the same command.
Modes: AI = LOCAL | CLOUD | HYBRID; Approval = SUGGEST | CONFIRM | AUTO (low-risk only) | MANUAL (AI cannot change anything).
Examples that work offline: `Outer ring ko 3.5mm Input C satin karo` - `Convert satin columns to Tatami fill with 0.40mm spacing, add 0.35mm pull compensation, auto-split stitches above 7.0mm, and apply edge-run + zigzag underlay for pique polo fabric.`
Setup for cloud: copy `.env.example` to `.env`, fill ONLY the keys you have (GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY), optionally set `*_MODEL`, set mode HYBRID or CLOUD in the dashboard. Check `/api/ai/status` (shows configured true/false, never keys).
CLI: `embcopilot ask "<prompt>" [--input art.png] [--ai-mode HYBRID] [--yes]`. API: see API.md.
Known gaps: width only for circular rings; "edge-run + zigzag" is applied as the engine's zigzag (which includes the edge run); Input C = satin column/ring; no streaming.
