# BASELINE TEST REPORT (before any change, input = phase-2 zip v0.2.0)

- Python 3.12 (sandbox). **pytest, ruff, mypy could not be installed** (no network). Tests were run with a
  minimal stand-in runner supporting `raises`, stacked `parametrize`, `approx` (not part of the project).
- Result: **179 passed, 0 failed, 0 skipped**, 0.4 s. Coverage: not measured.
- `python -m compileall`: clean.
- Pre-existing defects found by running the code (not by the tests): see AUDIT_REPORT.md B1–B8.
- Since then: 260 passed / 0 failed / 0 skipped (22 s). Please re-run with real pytest:
  `pip install -e .[dev] && pytest -q`.
