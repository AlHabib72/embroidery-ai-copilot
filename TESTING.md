# Testing
Real pytest: `pip install -e .[dev] && pytest -q`.  Offline fallback (what the author used): `PYTHONPATH=src python tools/mini_pytest.py [dir]`.
Dirs: tests/geometry, tests/vision, tests/export, tests/integration (+ v0.5 tests in tests/). Release commands: `pytest -q`, `python -m compileall src`, `embcopilot --version`,
`embcopilot capabilities`, `embcopilot benchmark`, `embcopilot web`. Visual regression: compare silhouette/holes/contours, not pixels (see golden/ + benchmark/benchmark.json).
Result at packaging time (mini_pytest stand-in, Python 3.12): **488 passed, 0 failed** (v0.5: 368, v0.6: +67, v0.7: +53). By directory: geometry 23, vision 4, export 11, integration 35, ai 39, emb 8. `python -m compileall src` OK. `tsc --strict` on web-next/src/lib OK. Real pytest was NOT available offline: re-run `pytest -q` yourself. No live LLM/Wilcom/browser tests.
Not tested: real browser, real Wilcom, SAM2/GPU, third-party DST/EXP readers, physical sew-outs.

New dirs: tests/ai (schema, parser, providers with fake transports, router fallback, policy, audit, session, controller), tests/emb (EMB Studio). Secrets: tests use synthetic fake keys only.
