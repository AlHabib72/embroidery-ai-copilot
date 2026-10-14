"""Zero-dependency HTTP server (stdlib only) + REST API + static single-page dashboard.

Security: binds to 127.0.0.1 by default; body size limit; MIME/content sniffing (not extension); safe per-job directories;
path-traversal protection; per-IP rate limiting; optional shared token (EMBCOPILOT_TOKEN) which is REQUIRED when binding to a
non-loopback address. Designs are never sent anywhere: everything runs locally."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..formats import capabilities
from ..segmentation import MODELS
from .jobs import JobError, JobManager

STATIC = os.path.join(os.path.dirname(__file__), "static")


class RateLimiter:
    def __init__(self, per_minute=240):
        self.per, self.hits, self.lock = per_minute, {}, threading.Lock()

    def ok(self, ip):
        now = time.time()
        with self.lock:
            q = [t for t in self.hits.get(ip, []) if now - t < 60]
            q.append(now)
            self.hits[ip] = q
            return len(q) <= self.per



class _AIState:
    """Process-wide AI config (never holds key material) + per-job session registry."""
    def __init__(self):
        from ..ai.providers import load_env
        load_env(".env")                                   # names only are loaded; values never printed
        from ..ai.router import AIRouter
        self.router = AIRouter(os.environ.get("EMBCOPILOT_AI_MODE", "LOCAL").upper() if os.environ.get("EMBCOPILOT_AI_MODE", "LOCAL").upper() in ("LOCAL", "CLOUD", "HYBRID") else "LOCAL")
        self.approval = "CONFIRM"
        self.lock = threading.Lock()

    def controller(self, jm, jid):
        from ..ai.controller import AIController
        from ..ai.session import DesignSession
        job = jm.get(jid)
        with self.lock:
            c = job.ctx.get("ai")
            if c is None:
                if not job.result or getattr(job.result, "plan", None) is None:
                    raise JobError("NOT_ANALYZED", "analyze the job first", 409)
                sess = DesignSession.from_pipeline(job.result)
                c = AIController(sess, self.router, self.approval, os.path.join(job.root, "ai"))
                job.ctx["ai"] = c
            c.router, c.approval_mode = self.router, self.approval
            return c


def make_handler(jm: JobManager, token: str | None, limiter: RateLimiter):
    AI = _AIState()
    class H(BaseHTTPRequestHandler):
        server_version = "EmbCopilot/" + __version__
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # structured job logs are written by JobManager; keep stdout quiet
            pass

        # -------------------------------------------------------- helpers
        def _send(self, code, body: bytes, ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, default=str).encode())

        def _err(self, e: JobError):
            self._json(e.http, {"error": e.code, "message": e.msg})

        def _body(self, limit) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise JobError("FILE_TOO_LARGE", f"limit is {limit // (1024*1024)} MB", 413)
            return self.rfile.read(n) if n else b""

        def _auth(self) -> bool:
            if not token:
                return True
            q = parse_qs(urlparse(self.path).query)
            return self.headers.get("X-Token") == token or (q.get("token") or [""])[0] == token

        def _dispatch(self, method):
            u = urlparse(self.path)
            path = unquote(u.path)
            if not limiter.ok(self.client_address[0]):
                return self._json(429, {"error": "RATE_LIMITED", "message": "too many requests"})
            if path.startswith("/api/") and not self._auth():
                return self._json(401, {"error": "UNAUTHORIZED", "message": "token required"})
            try:
                if method == "GET" and path in ("/", "/index.html"):
                    return self._send(200, open(os.path.join(STATIC, "index.html"), "rb").read(), "text/html; charset=utf-8")
                if method == "GET" and path == "/api/capabilities":
                    from ..emb import EMBAdapter
                    return self._json(200, {"version": __version__, "formats": {k: v.to_dict() for k, v in capabilities().items()},
                                            "models": [m.to_dict() for m in MODELS.status()], "emb": EMBAdapter().status().to_dict(),
                                            "wilcom": {"es65": "UNAVAILABLE (interface + MOCK only)", "e4": "PLANNED", "mock": "MOCK"},
                                            "accepted_uploads": sorted(__import__("embcopilot.inputs", fromlist=["ACCEPTED"]).ACCEPTED)})
                if method == "GET" and path == "/api/ai/status":
                    return self._json(200, {**AI.router.status(), "approval_mode": AI.approval, "approval_modes": ["SUGGEST", "CONFIRM", "AUTO", "MANUAL"]})
                if method == "POST" and path == "/api/ai/config":
                    body = json.loads(self._body(10_000) or b"{}")
                    from ..ai.router import AIRouter, MODES as AIMODES
                    from ..ai.policy import MODES as PMODES
                    if body.get("ai_mode"):
                        m = str(body["ai_mode"]).upper()
                        if m not in AIMODES:
                            raise JobError("BAD_MODE", f"ai_mode must be one of {AIMODES}", 400)
                        AI.router = AIRouter(m, AI.router.order, AI.router.providers[AI.router.order[0]].transport, AI.router.providers[AI.router.order[0]].sleep, AI.router.timeout)
                    if body.get("approval_mode"):
                        m = str(body["approval_mode"]).upper()
                        if m not in PMODES:
                            raise JobError("BAD_MODE", f"approval_mode must be one of {PMODES}", 400)
                        AI.approval = m
                    return self._json(200, {**AI.router.status(), "approval_mode": AI.approval})   # keys can NOT be set through the API: environment only
                if method == "POST" and path == "/api/ai/command":
                    from ..ai import policy as _pol
                    body = json.loads(self._body(50_000) or b"{}")
                    r = AI.router.parse(str(body.get("text", "")), [str(x)[:40] for x in (body.get("roles") or [])][:60])
                    d = _pol.assess(r.steps, AI.approval)
                    return self._json(200, {"router": r.to_dict(), "decision": d.to_dict(), "executed": False,
                                            "note": "parse-only (no job attached); use /api/jobs/{id}/ai/command to preview and apply"})
                if method == "GET" and path == "/api/emb/capabilities":
                    from ..emb_studio import emb_capabilities
                    return self._json(200, emb_capabilities())
                if method == "POST" and path == "/api/emb/inspect":
                    from ..emb_studio import inspect_emb_bytes, MAX_EMB_BYTES
                    data = self._body(min(jm.max_bytes, MAX_EMB_BYTES))
                    return self._json(200, inspect_emb_bytes(data, re.sub(r"[^A-Za-z0-9._-]", "_", self.headers.get("X-Filename", "upload.emb"))[:100]))
                if method == "GET" and path == "/api/v06/capabilities":
                    from ..cli_v06 import capabilities_report
                    return self._json(200, capabilities_report())
                if method == "GET" and path == "/api/fonts":
                    from ..fonts import scan_fonts
                    q = parse_qs(u.query); lim = max(1, min(int((q.get("limit") or ["200"])[0]), 2000))
                    return self._json(200, {"fonts": [f.to_dict() for f in scan_fonts(limit=lim)],
                                            "note": "system/user fonts only; no commercial fonts are bundled"})
                if method == "GET" and path == "/api/fabrics":
                    from ..fabrics import FABRICS
                    return self._json(200, {"fabrics": {k: {kk: vv for kk, vv in v.__dict__.items()} for k, v in FABRICS.items()},
                                            "note": "heuristic profiles, not fitted to sew-outs"})
                if method == "GET" and path == "/api/threads":
                    from ..threads import ThreadDatabase
                    db = ThreadDatabase.generic()
                    return self._json(200, {"brands": db.brands, "note": "generic palette only; import your licensed CSV for Madeira/Isacord/Gunold"})
                if method == "GET" and path == "/api/machines":
                    from ..machine import MACHINES
                    return self._json(200, {k: v.to_dict() for k, v in MACHINES.items()})
                if method == "POST" and path == "/api/commands":
                    from ..command_v2 import parse_multi_command
                    body = json.loads(self._body(100_000) or b"{}")
                    cp = parse_multi_command(str(body.get("text", ""))[:600], body.get("detected_roles"))
                    return self._json(200, {**cp.to_dict(), "executed": False, "note": "parsed to a validated plan only; nothing was executed"})
                if method == "GET" and path == "/api/emb/status":
                    from ..nativeemb import NativeEMBAdapter
                    a = NativeEMBAdapter()
                    return self._json(200, {"capability": a.detect_capability(), "ui": a.ui_message()})
                if method == "GET" and path == "/api/options":
                    from ..config import list_fabrics
                    from ..fabrics import FABRICS, HOOPS
                    from ..profiles import PROFILES
                    from ..threads import ThreadDatabase
                    return self._json(200, {"fabrics": sorted(FABRICS), "profiles": sorted(PROFILES), "hoops": sorted(HOOPS),
                                            "brands": ThreadDatabase.generic().brands, "colorway_modes": ["nearest", "min_threads"],
                                            "stitch_overrides": ["run", "triple_run", "satin", "tatami", "complex_fill", "applique", "puff", "cross_stitch"]})
                if method == "POST" and path == "/api/jobs":
                    data = self._body(jm.max_bytes)
                    job = jm.create(self.headers.get("X-Filename", "upload"), data)
                    return self._json(201, jm.summary(job.job_id))
                m = re.match(r"^/api/jobs/([A-Za-z0-9]+)(?:/(.*))?$", path)
                if m:
                    jid, rest = m.group(1), (m.group(2) or "")
                    if method == "GET" and rest == "":
                        return self._json(200, jm.summary(jid))
                    if method == "DELETE" and rest == "":
                        jm.get(jid); jm.delete(jid); return self._json(200, {"deleted": jid})
                    if rest.startswith("ai/"):
                        sub = rest[3:]
                        if method == "GET" and sub == "summary":
                            return self._json(200, AI.controller(jm, jid).session.summary())
                        if method == "GET" and sub == "history":
                            c = AI.controller(jm, jid)
                            return self._json(200, {"history": c.session.history[-100:], "audit_records": len(c.session.audit.records)})
                        if method == "POST" and sub == "command":
                            body = json.loads(self._body(50_000) or b"{}")
                            c = AI.controller(jm, jid)
                            return self._json(200, c.propose(str(body.get("text", "")), bool(body.get("preview", True))))
                        if method == "POST" and sub == "approve":
                            body = json.loads(self._body(50_000) or b"{}")
                            try:
                                p = AI.controller(jm, jid).approve(str(body.get("proposal_id", "")), body.get("exclude"), body.get("edits"))
                            except KeyError:
                                raise JobError("UNKNOWN_PROPOSAL", "no such proposal", 404)
                            except ValueError as e:
                                raise JobError("INVALID_APPROVAL", str(e), 400)
                            return self._json(200, {"proposal": p, "summary": AI.controller(jm, jid).session.summary()})
                        if method == "POST" and sub == "reject":
                            body = json.loads(self._body(10_000) or b"{}")
                            try:
                                return self._json(200, AI.controller(jm, jid).reject(str(body.get("proposal_id", ""))))
                            except KeyError:
                                raise JobError("UNKNOWN_PROPOSAL", "no such proposal", 404)
                        if method == "POST" and sub in ("undo", "redo"):
                            c = AI.controller(jm, jid)
                            m = c.session.undo() if sub == "undo" else c.session.redo()
                            return self._json(200, {"changed": m is not None, "summary": c.session.summary()})
                        if method == "POST" and sub == "visual-qc":
                            body = json.loads(self._body(10_000) or b"{}")
                            if body.get("consent") is not True:
                                return self._json(409, {"error": "CONSENT_REQUIRED", "message": "Visual QC uploads a rendered preview image to Gemini. Send {\"consent\": true} to allow this one call."})
                            from ..ai.providers import GeminiProvider, ProviderError
                            from ..exportpkg import render_png
                            c = AI.controller(jm, jid)
                            png = os.path.join(job_dir := os.path.join(jm.base, jid), "ai", "visual_qc.png")
                            os.makedirs(os.path.dirname(png), exist_ok=True)
                            render_png(c.session.build.plan, png)
                            try:
                                out = GeminiProvider().analyze_visual(open(png, "rb").read(), "stitch render of an embroidery design; report density gaps, crossings, tiny details", True)
                            except ProviderError as e:
                                return self._json(502 if e.code != "NOT_CONFIGURED" else 409, {"error": e.code, "message": str(e)})
                            return self._json(200, out)
                    if method == "POST" and rest == "emb/handoff":
                        from ..emb_studio import handoff_bundle
                        c = AI.controller(jm, jid)
                        out = handoff_bundle(c.session.build, os.path.join(jm.base, jid, "emb_handoff"), "design")
                        return self._json(200, {**out, "artifact_prefix": "emb_handoff/"})
                    if method == "POST" and rest == "qc2":
                        job = jm.get(jid)
                        if not job.result or job.result.plan is None:
                            raise JobError("NOT_ANALYZED", "analyze first", 409)
                        from ..production import production_pass
                        body = json.loads(self._body(100_000) or b"{}")
                        out = os.path.join(jm.base, jid, "v06_package")
                        ov = body.get("expert_override") if isinstance(body.get("expert_override"), dict) else None
                        res = production_pass(job.result, str(body.get("machine", "generic_flat")), out, ("dst", "exp", "pes", "emb"), ov)
                        return self._json(200, {"result": res.to_dict(), "wording": "production-oriented; QC validated; requires sew-out validation"})
                    if method == "POST" and rest == "emb/export":
                        from ..nativeemb import NativeEMBAdapter
                        a = NativeEMBAdapter()
                        return self._json(409, {"status": "EMB_EXPORT_UNAVAILABLE", "ui": a.ui_message(), "capability": a.detect_capability(),
                                                "download": False})
                    if method == "POST" and rest == "analyze":
                        body = json.loads(self._body(1_000_000) or b"{}")
                        jm.analyze(jid, body)
                        return self._json(202, jm.summary(jid))
                    if method == "POST" and rest == "cancel":
                        jm.get(jid).cancel.set(); return self._json(200, {"cancelling": True})
                    if method == "POST" and rest == "repair":
                        body = json.loads(self._body(100_000) or b"{}")
                        r = jm.repair(jid, list(body.get("apply", [])))
                        return self._json(200, {**r, "job": jm.summary(jid)})
                    if method == "POST" and rest == "approve":
                        body = json.loads(self._body(100_000) or b"{}")
                        jm.approve(jid, bool(body.get("approved", True)), str(body.get("note", "")))
                        return self._json(200, jm.summary(jid))
                    if method == "POST" and rest.startswith("export/"):
                        info = jm.export(jid, rest.split("/", 1)[1])
                        return self._json(200, {"export": info, "job": jm.summary(jid)})
                    if method == "POST" and rest == "segment":
                        prompts = json.loads(self._body(100_000) or b"{}")
                        from ..segmentation import SegmentationUnavailable, SAM2Segmentation
                        try:
                            SAM2Segmentation()._predictor()
                        except SegmentationUnavailable as e:
                            return self._json(501, {"error": "SAM2_UNAVAILABLE", "message": str(e), "fallback": "Standard vision remains active"})
                        return self._json(501, {"error": "SAM2_INTERACTIVE_NOT_WIRED", "message": "SAM2 is present but interactive refinement is EXPERIMENTAL and not wired in this build"})
                    if method == "POST" and rest == "command":
                        body = json.loads(self._body(100_000) or b"{}")
                        job = jm.get(jid)
                        if not job.result:
                            raise JobError("NOT_ANALYZED", "analyze first", 409)
                        from ..command import CommandParser
                        cp = CommandParser().parse(str(body.get("text", ""))[:300])
                        return self._json(200, {"structured": cp.__dict__, "executed": False, "requires_approval": True,
                                                "note": "parsed to a structured intent only; nothing was changed or sent to any application"})
                    if method == "GET" and rest == "zip":
                        p = jm.zip_job(jid)
                        return self._send(200, open(p, "rb").read(), "application/zip", {"Content-Disposition": f'attachment; filename="job_{jid}.zip"'})
                    if method == "GET" and rest.startswith("artifacts/"):
                        p = jm.artifact_path(jid, rest[len("artifacts/"):])
                        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
                        extra = {"Content-Disposition": f'attachment; filename="{os.path.basename(p)}"'} if p.split(".")[-1] in ("dst", "exp", "zip") else {}
                        if p.endswith(".html"):
                            extra["Content-Security-Policy"] = "sandbox allow-scripts"
                        return self._send(200, open(p, "rb").read(), ctype, extra)
                return self._json(404, {"error": "NOT_FOUND", "message": path})
            except JobError as e:
                self._err(e)
            except json.JSONDecodeError:
                self._json(400, {"error": "BAD_JSON", "message": "request body is not valid JSON"})
            except Exception as e:  # noqa: BLE001
                self._json(500, {"error": "INTERNAL", "message": f"{type(e).__name__}: {e}"})

        def do_GET(self): self._dispatch("GET")
        def do_POST(self): self._dispatch("POST")
        def do_DELETE(self): self._dispatch("DELETE")

    return H


def make_server(host="127.0.0.1", port=8765, work_dir=None, token=None, max_mb=50):
    if host not in ("127.0.0.1", "localhost", "::1") and not (token or os.environ.get("EMBCOPILOT_TOKEN")):
        raise SystemExit("Refusing to listen on a non-loopback address without EMBCOPILOT_TOKEN / --token set.")
    token = token or os.environ.get("EMBCOPILOT_TOKEN")
    jm = JobManager(work_dir or os.path.join(tempfile.gettempdir(), "embcopilot_jobs"), max_bytes=max_mb * 1024 * 1024)
    srv = ThreadingHTTPServer((host, port), make_handler(jm, token, RateLimiter()))
    srv.jobs = jm
    return srv


def serve(host="127.0.0.1", port=8765, work_dir=None, token=None, open_browser=True):
    srv = make_server(host, port, work_dir, token)
    url = f"http://{host}:{srv.server_address[1]}/"
    print(f"Embroidery AI Copilot {__version__} dashboard: {url}  (Ctrl+C to stop)")
    print("Runs fully locally: designs are not uploaded anywhere.")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
