import json, os, tempfile, threading, time, urllib.error, urllib.request
from embcopilot.web.server import make_server
from embcopilot.golden import write_golden

def _srv():
    srv = make_server(port=0, work_dir=tempfile.mkdtemp())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]

def _g(port, path, data=None, method=None, headers=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

def _job(port):
    png = open(write_golden(tempfile.mkdtemp())["12_multi_object_logo"], "rb").read()
    jid = _g(port, "/api/jobs", png, "POST", {"X-Filename": "a.png"})[1]["job_id"]
    assert _g(port, f"/api/jobs/{jid}/analyze", json.dumps({"size_mm": [100, 100]}).encode(), "POST")[0] == 202
    for _ in range(90):
        if _g(port, f"/api/jobs/{jid}")[1].get("status") in ("review", "failed"): break
        time.sleep(1)
    return jid

def test_ai_status_never_leaks_keys_and_config_rejects_bad_modes():
    os.environ["GROQ_API_KEY"] = "gsk_" + "Z" * 30
    srv, port = _srv()
    try:
        st, j = _g(port, "/api/ai/status")
        assert st == 200 and j["providers"]["groq"]["configured"] is True and "Z" * 30 not in json.dumps(j)
        assert _g(port, "/api/ai/config", json.dumps({"ai_mode": "ROGUE"}).encode(), "POST")[0] == 400
        assert _g(port, "/api/ai/config", json.dumps({"approval_mode": "YOLO"}).encode(), "POST")[0] == 400
        st, j = _g(port, "/api/ai/config", json.dumps({"api_key": "x", "ai_mode": "HYBRID"}).encode(), "POST")
        assert st == 200 and j["mode"] == "HYBRID" and "api_key" not in json.dumps(j)      # keys cannot be set through the API
    finally:
        srv.shutdown(); os.environ.pop("GROQ_API_KEY", None)

def test_global_command_is_parse_only_and_emb_endpoints():
    srv, port = _srv()
    try:
        st, j = _g(port, "/api/ai/command", json.dumps({"text": "outer ring 3mm satin", "roles": ["outer_ring"]}).encode(), "POST")
        assert st == 200 and j["executed"] is False and j["router"]["steps"][0]["op"] == "set_stitch_type"
        st, j = _g(port, "/api/emb/capabilities"); assert j["EMB_CREATE"]["state"] == "REQUIRES_WILCOM_CONFIGURATION"
        st, j = _g(port, "/api/emb/inspect", b"LA:abc" + b"\0" * 600, "POST", {"X-Filename": "../../etc/passwd.emb"})
        assert st == 200 and j["classification"] == "RENAMED_DST" and "/" not in j["filename"]
    finally:
        srv.shutdown()

def test_job_flow_propose_approve_undo_handoff():
    srv, port = _srv()
    try:
        jid = _job(port)
        st, p = _g(port, f"/api/jobs/{jid}/ai/command", json.dumps({"text": "0.5mm spacing on the text and auto-split above 5mm"}).encode(), "POST")
        assert st == 200 and p["status"] == "pending" and p["chips"] and p["router"]["provider"] == "local-rules"
        before = _g(port, f"/api/jobs/{jid}/ai/summary")[1]
        assert before["can_undo"] is False
        st, r = _g(port, f"/api/jobs/{jid}/ai/approve", json.dumps({"proposal_id": p["proposal_id"]}).encode(), "POST")
        assert st == 200 and r["proposal"]["status"] == "applied" and r["summary"]["can_undo"]
        assert _g(port, f"/api/jobs/{jid}/ai/approve", json.dumps({"proposal_id": p["proposal_id"]}).encode(), "POST")[0] == 400   # no double apply
        assert _g(port, f"/api/jobs/{jid}/ai/approve", json.dumps({"proposal_id": "nope"}).encode(), "POST")[0] == 404
        assert _g(port, f"/api/jobs/{jid}/ai/undo", b"{}", "POST")[1]["changed"] is True
        assert _g(port, f"/api/jobs/{jid}/ai/history")[1]["history"]
        st, h = _g(port, f"/api/jobs/{jid}/emb/handoff", b"{}", "POST")
        assert st == 200 and h["not_an_emb"] and not any(f.endswith(".emb") for f in h["files"])
        st, e = _g(port, f"/api/jobs/{jid}/emb/export", b"{}", "POST")
        assert st == 409 and e["download"] is False
        st, v = _g(port, f"/api/jobs/{jid}/ai/visual-qc", b"{}", "POST")
        assert st == 409 and v["error"] == "CONSENT_REQUIRED"                                  # no image leaves without consent
        st, v = _g(port, f"/api/jobs/{jid}/ai/visual-qc", json.dumps({"consent": True}).encode(), "POST")
        assert st == 409 and v["error"] == "NOT_CONFIGURED"                                     # consent given, but no Gemini key yet
    finally:
        srv.shutdown()

def test_ai_endpoints_require_analysis_first():
    srv, port = _srv()
    try:
        png = open(write_golden(tempfile.mkdtemp())["01_circle"], "rb").read()
        jid = _g(port, "/api/jobs", png, "POST", {"X-Filename": "a.png"})[1]["job_id"]
        assert _g(port, f"/api/jobs/{jid}/ai/command", json.dumps({"text": "x"}).encode(), "POST")[0] == 409
    finally:
        srv.shutdown()

def test_dashboard_contains_prompt_box_and_no_secrets():
    html = open(os.path.join(os.path.dirname(__file__), "..", "..", "src", "embcopilot", "web", "static", "index.html"), encoding="utf-8").read()
    for el in ('id="pbText"', 'id="pbOk"', 'id="pbChips"', 'id="aiMode"', 'id="apMode"'):
        assert el in html
    import re
    assert not re.search(r"gsk_[A-Za-z0-9]{10,}|sk-or-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}", html)


def _cli(*a):
    import contextlib, io
    from embcopilot.cli import main
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(list(a))
    return rc, buf.getvalue()

def test_cli_ask_preview_then_apply_and_emb_info():
    rc, out = _cli("ask", "outer ring 2mm satin, 0.45mm spacing on the text")
    assert rc == 0 and "local-rules" in out and "preview only" in out and "APPLIED" not in out
    rc, out = _cli("ask", "outer ring 2mm satin", "--yes")
    assert rc == 0 and "APPLIED" in out
    rc, out = _cli("emb-info"); j = json.loads(out)
    assert j["EMB_EXPORT"]["state"] == "REQUIRES_WILCOM_CONFIGURATION"
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "fake.emb"); open(p, "wb").write(b"LA:x" + b"\0" * 600)
        rc, out = _cli("emb-info", p); assert json.loads(out)["classification"] == "RENAMED_DST"
        rc, out = _cli("emb-handoff", "--out-dir", os.path.join(d, "h"))
        assert rc == 0 and "NOT an EMB file" in out and not any(f.endswith(".emb") for f in os.listdir(os.path.join(d, "h")))
