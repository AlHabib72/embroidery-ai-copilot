"""One canonical version: package, CLI, pyproject must agree (audit item A1)."""
import io, contextlib, os, re
import embcopilot
from embcopilot.cli import main

ROOT = os.path.dirname(os.path.dirname(__file__))

def test_cli_version_matches_package():
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            main(["--version"])
    except SystemExit:
        pass
    assert embcopilot.__version__ in buf.getvalue()

def test_pyproject_has_no_hardcoded_version():
    text = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    assert not re.search(r'^version\s*=\s*"', text, re.M)
    assert 'attr = "embcopilot.__version__"' in text
    assert 'dynamic = ["version"]' in text
