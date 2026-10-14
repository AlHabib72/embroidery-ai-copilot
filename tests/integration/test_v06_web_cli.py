import io, contextlib, json, os, tempfile, threading, urllib.request, urllib.error
from embcopilot.cli import main
from embcopilot.web.server import make_server
import embcopilot

def _cli(*a):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = main(list(a))
    return rc, buf.getvalue()

def test_version_is_060():
    assert embcopilot.__version__ == "0.7.0"

def test_cli_capabilities_honest():
    rc, out = _cli("capabilities"); j = json.loads(out)
    assert rc == 0 and j["version"] == "0.7.0"
    assert j["features"]["native_emb"] == "REQUIRES_WILCOM_CONFIGURATION"
    assert j["features"]["physical_sewout_simulation"] == "UNAVAILABLE" and j["features"]["PES"] == "UNAVAILABLE"
    assert j["native_emb"]["button"]["label"] == "Configure EMB Export"

def test_cli_command_parses_without_executing():
    rc, out = _cli("command", "Outer ring ko 3.5mm Input C satin karo"); j = json.loads(out)
    assert j["executes_code"] is False and j["steps"][0]["changes"]["width_mm"] == 3.5

def test_cli_text_check():
    rc, out = _cli("text-check", "OBA", "--cap-mm", "3"); j = json.loads(out)
    assert rc == 0 and j["status"] in ("WARNING", "UNSUITABLE")

def test_cli_badge_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        rc, out = _cli("badge", "--out-dir", d)
        assert rc == 0 and "design.emb: REQUIRES_WILCOM_CONFIGURATION" in out and "design.dst: EXPORTED_VERIFIED" in out
        for f in ("design.dst", "design.exp", "design.svg", "preview.png", "qc.json", "metadata.json", "production_report.json", "badge_info.json"):
            assert os.path.exists(os.path.join(d, f)), f
        assert not os.path.exists(os.path.join(d, "design.emb")) and not os.path.exists(os.path.join(d, "design.pes"))

def _srv():
    srv = make_server(port=0, work_dir=tempfile.mkdtemp())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]

def _get(port, path, data=None, method=None, headers=None):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

def test_web_v06_endpoints():
    srv, port = _srv()
    try:
        assert _get(port, "/api/fonts?limit=2")[1]["fonts"]
        assert _get(port, "/api/emb/status")[1]["capability"]["button"]["enabled"] is False
        st, j = _get(port, "/api/commands", json.dumps({"text": "delete background and make outer ring 3mm satin"}).encode(), "POST")
        assert st == 200 and j["executed"] is False and len(j["steps"]) == 2
        assert "generic_flat" in _get(port, "/api/machines")[1] and _get(port, "/api/threads")[1]["brands"]
        assert "DENIM" in _get(port, "/api/fabrics")[1]["fabrics"]
    finally:
        srv.shutdown()

def test_web_qc2_and_emb_export_refusal():
    from embcopilot.golden import write_golden
    srv, port = _srv()
    try:
        png = open(write_golden(tempfile.mkdtemp())["12_multi_object_logo"], "rb").read()
        st, j = _get(port, "/api/jobs", png, "POST", {"X-Filename": "a.png"}); jid = j["job_id"]
        # job must be analysed first
        assert _get(port, f"/api/jobs/{jid}/qc2", b"{}", "POST")[0] == 409
        st, j = _get(port, f"/api/jobs/{jid}/emb/export", b"{}", "POST")
        assert st == 409 and j["download"] is False and j["ui"]["title"] == "Native EMB Export Unavailable"
    finally:
        srv.shutdown()
