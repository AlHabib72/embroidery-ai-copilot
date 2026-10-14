"""Minimal offline stand-in for pytest (used ONLY when real pytest is not installed).

Supports: plain test_ functions, pytest.raises, mark.parametrize, fixture(no-op),
approx, skip.  Real pytest is still the recommended runner:  pip install -e .[dev]

Usage:  python tools/mini_pytest.py [tests/dir_or_file ...]
"""
from __future__ import annotations
import importlib.util, inspect, itertools, os, sys, time, traceback, types

class _Skip(Exception): pass

class _Raises:
    def __init__(self, exc): self.exc = exc; self.value = None
    def __enter__(self): return self
    def __exit__(self, t, v, tb):
        if t is None: raise AssertionError(f"DID NOT RAISE {self.exc}")
        if issubclass(t, self.exc): self.value = v; return True
        return False

def raises(exc, func=None, *a, **k):
    ctx = _Raises(exc)
    if func is None: return ctx
    with ctx: func(*a, **k)
    return ctx

class _Approx:
    def __init__(self, v, rel=1e-6, abs=1e-12): self.v, self.rel, self.abs = v, rel, abs
    def __eq__(self, o): return abs(o - self.v) <= max(self.rel * abs(self.v), self.abs)

class _Mark:
    @staticmethod
    def parametrize(names, values):
        def deco(f):
            f._params = getattr(f, "_params", []) + [(names, list(values))]; return f
        return deco
    def __getattr__(self, n): return lambda *a, **k: (lambda f: f)

def _install_shim():
    m = types.ModuleType("pytest")
    m.raises, m.approx, m.mark = raises, _Approx, _Mark()
    m.fixture = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda f: f))
    def skip(msg=""): raise _Skip(msg)
    m.skip = skip
    sys.modules["pytest"] = m

def main(argv):
    try:
        import pytest  # noqa
        real = hasattr(sys.modules["pytest"], "main")
    except ImportError:
        real = False
    if not real: _install_shim()
    targets = argv or ["tests"]
    files = []
    for t in targets:
        if os.path.isdir(t):
            for r, _, fs in os.walk(t):
                files += [os.path.join(r, f) for f in sorted(fs) if f.startswith("test_") and f.endswith(".py")]
        else: files.append(t)
    passed = failed = skipped = 0; fails = []; t0 = time.time()
    for fp in sorted(files):
        spec = importlib.util.spec_from_file_location(os.path.basename(fp)[:-3], fp)
        mod = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.dirname(os.path.abspath(fp)))
        try: spec.loader.exec_module(mod)
        except Exception:
            failed += 1; fails.append((fp + " (import)", traceback.format_exc())); continue
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not name.startswith("test_") or fn.__module__ != mod.__name__: continue
            cases = [()]
            if hasattr(fn, "_params"):
                groups = []
                for names, vals in reversed(fn._params):
                    n = [x.strip() for x in names.split(",")]
                    groups.append([tuple(v) if len(n) > 1 else (v,) for v in vals])
                cases = [sum(c, ()) for c in itertools.product(*groups)]
            for c in cases:
                try: fn(*c); passed += 1
                except _Skip: skipped += 1
                except Exception: failed += 1; fails.append((f"{fp}::{name}{c}", traceback.format_exc()))
    for n, tb in fails: print("FAIL", n); print(tb[-1500:])
    print(f"{passed} passed, {failed} failed, {skipped} skipped in {time.time()-t0:.1f}s  (mini_pytest stand-in)")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
