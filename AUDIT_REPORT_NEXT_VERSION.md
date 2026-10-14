# AUDIT REPORT - v0.6.0 -> v0.7.0 (focus: Prompt Box + EMB handling)

Scope requested: "next version special for the Prompt Box and EMB file". Everything else in the v0.7 master spec (Next.js studio, WebGL canvas, SAM2 execution, PES/JEF/VP3, full canvas editor)
was **not** attempted - see the matrix. Method: read the v0.6 tree I built, the web server/job manager, strategies/technical blueprint hooks, then implemented, tested and measured.

## Findings in v0.6 (before this work)
| # | Finding | State | v0.7 action |
|---|---|---|---|
| 1 | Command parser (`command_v2`) was parse-only, English/Hinglish keywords, not applied to a design | PARTIAL | `ai/` package: schema, router, policy, session, controller, audit - steps now really change the design |
| 2 | No LLM path at all | MISSING | Groq/Gemini/OpenRouter clients (env-only keys), router with recorded fallback |
| 3 | `satin_ring` **ignored the requested underlay** (every ring got a centre run) | **BROKEN (found while testing "edge-run + zigzag")** | fixed in `strategies.py`: edge run / zigzag / double zigzag now generated for rings |
| 4 | "Reduce trims" had no mechanism (travel policy only added trims) | MISSING | `prefer_fewer_trims` in `travel.optimize_travel` (longer hidden detours accepted) |
| 5 | EMB: one capability flag + stub adapter | PARTIAL | six separate capabilities, safe inspection, handoff bundle |
| 6 | Dashboard had a parse-only command box | PARTIAL | full prompt box: chips, edit/drop chips, risk, approve/reject, undo/redo, mode selectors, provider badge, privacy line |
| 7 | Key handling | n/a | `.env.example`, `.gitignore`, redaction everywhere, keys never in API/UI/logs |

## SECURITY NOTICE (action needed by you)
The spec document you attached contained **three real-looking API keys in plain text**. I did not copy them into any file, test, doc or log, and did not use them.
Treat them as compromised: **revoke/rotate them at the providers now**, then put the new keys only in your local `.env` (git-ignored) or environment variables.
I cannot verify whether those keys were valid; rotating is the safe default either way.

## Regression
v0.6 baseline 435 tests -> after v0.7: see TESTING.md (all green at packaging time, including after the ring-underlay fix).
Intentional behaviour change: rings now carry their real underlay, so ring stitch counts rose (e.g. `02_ring` 1256 -> 1602; vector badge 5001 -> 5440). No v0.6 test needed editing except the version assertion.

## Not verified (be aware)
* No live call to Groq/Gemini/OpenRouter was possible (no network, no keys): providers are tested with fake transports. **Default model names are unverified guesses** - set `GROQ_MODEL`/`GEMINI_MODEL`/`OPENROUTER_MODEL`.
* The OpenAI-compatible request shape and Gemini `generateContent` shape follow public docs from memory; first live call may need a small adjustment.
* Dashboard JS is syntax-checked and its endpoints tested, but it was never opened in a real browser.
* Latency claims ("<200 ms Groq", "<100 ms canvas") are NOT claimed: measured local parse time is ~4 ms (rules); cloud latency is recorded per call, unmeasured here.
