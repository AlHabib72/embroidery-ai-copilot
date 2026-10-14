# Security notes (v0.7)
* Keys: environment / local `.env` only (`.env` is git-ignored; `.env.example` has placeholders). Never in code, tests, docs, logs, API responses or the browser. `providers.redact()` scrubs key shapes and env values from errors, audit records and logs.
* If a key was ever pasted into a chat, document or repo, **rotate it**.
* Cloud = optional. LOCAL mode sends nothing anywhere. Cloud text parsing sends only prompt text + role names. Images go to Gemini only through `visual-qc` with `{"consent": true}` per call.
* LLM output is data: validated against a closed schema, ranges enforced, policy decided in code, human approval by default, audit-logged. No eval/exec/shell/path from model output.
* Uploads: size cap, extension and content checks, sanitized names, job-scoped directories (v0.5 server), EMB inspection reads bytes only (bounded, no parsing of internals, no extraction of archives).
* Not done: authentication beyond the optional token, TLS (run behind a reverse proxy), per-user isolation.
