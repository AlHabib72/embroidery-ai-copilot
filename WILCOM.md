# Wilcom integration (v0.6)
State machine: NOT_CONFIGURED → CONNECTING → CONNECTED → READY → DESIGN_OPEN → EXECUTING → VERIFYING (ERROR / STOPPED any time); illegal transitions raise.
`emergency_stop()` blocks creation. Priority of routes (when you implement one): supported web/API → supported SDK → desktop automation → accessibility → keyboard → UI recognition → coordinates last.
Rules: plan → user approval → execute → observe → verify → record; no LLM→mouse; no licence bypass; no format reverse engineering.
Status in this build: adapter + state machine IMPLEMENTED; any real Wilcom route REQUIRES_CONFIGURATION and is NOT implemented; v0.5 mock adapter remains MOCK. Tested Wilcom version: none.
