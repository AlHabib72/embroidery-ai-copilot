import http.client, json, os, re, subprocess, tempfile, threading, time
import cv2, numpy as np
import pytest
from embcopilot.segmentation import (MODELS, get_provider, objects_from_masks, clean_mask, SAM2Segmentation, SegmentationUnavailable, ClassicalSegmentation)
from embcopilot.pipeline import run_pipeline
from embcopilot.web.server import make_server
from embcopilot.web.jobs import JobManager, JobError

HERE = os.path.dirname(__file__)
SAMPLE = os.path.join(HERE, "..", "examples", "composite.png")
DST = os.path.join(HERE, "..", "samples", "hummingbird_sample.dst")

# ------------------------------------------------------------ segmentation / models
def test_model_status_is_truthful_without_torch():
    st = {m.name: m for m in MODELS.status()}
    assert st["Standard (Vision V2)"].state == "INSTALLED"
    assert st["SAM 2"].state == "UNAVAILABLE" and "missing" in st["SAM 2"].detail
    assert st["Advanced semantic model"].state == "UNAVAILABLE"

def test_sam2_request_falls_back_and_says_so_instead_of_crashing():
    prov, mode, warns = get_provider("sam2")
    assert isinstance(prov, ClassicalSegmentation) and "unavailable" in mode.lower() and warns and "SAM2_UNAVAILABLE" in warns[0]
    r = run_pipeline(SAMPLE, (100, 100), segmentation="sam2")
    assert r.stopped is None and r.vision_mode.startswith("Standard") and len(r.objects) >= 4

def test_sam2_provider_raises_unavailable_not_import_error():
    with pytest.raises(SegmentationUnavailable):
        SAM2Segmentation().masks(SAMPLE)

def test_mask_cleanup_removes_specks_fills_pinholes_keeps_real_holes():
    m = np.zeros((300, 300), np.uint8); cv2.circle(m, (150, 150), 100, 1, -1); cv2.circle(m, (150, 150), 50, 0, -1)
    m[10:13, 10:13] = 1; m[150, 100] = 1; m[200, 150] = 0; m[20, 150] = 1
    c = clean_mask(m)
    assert c[11, 11] == 0 and c[200, 150] == 255 and c[150, 150] == 0        # speck gone, pinhole filled, real hole kept

def test_objects_from_masks_gives_holes_and_resolves_overlap_to_smaller_mask():
    img = np.full((300, 300, 3), 255, np.uint8); cv2.circle(img, (150, 150), 100, (0, 0, 200), -1); cv2.circle(img, (150, 150), 40, (200, 0, 0), -1)
    big = np.zeros((300, 300), np.uint8); cv2.circle(big, (150, 150), 100, 1, -1)
    small = np.zeros((300, 300), np.uint8); cv2.circle(small, (150, 150), 40, 1, -1)
    bp = objects_from_masks([big.astype(bool), small.astype(bool)], img, (75, 75))
    assert len(bp.objects) == 2
    outer = max(bp.objects, key=lambda o: o.area_mm2)
    assert len(outer.holes_mm) == 1                                              # the small mask's area is a hole in the big one
    assert abs(sum(o.area_mm2 for o in bp.objects) - np.pi * 25 ** 2 * 100 / 100 * 1.0 * 1.0) / (np.pi * 25 ** 2) < 3.5

def test_model_manager_unload_and_memory_guard():
    MODELS.unload()
    calls = []
    obj = MODELS.get("dummy", lambda: calls.append(1) or object(), need_gb=0.0); assert MODELS.get("dummy", lambda: 1 / 0, need_gb=0.0) is obj and len(calls) == 1   # cached
    MODELS.unload("dummy"); MODELS.get("dummy", lambda: calls.append(1) or object(), need_gb=0.0); assert len(calls) == 2                                     # reloaded after unload
    MODELS.unload()
    with pytest.raises(SegmentationUnavailable):
        MODELS.get("huge", lambda: object(), need_gb=1e6)                                                                                                    # memory guard

# ------------------------------------------------------------------- web API
@pytest.fixture(scope="module") if hasattr(pytest, "fixture") else (lambda f: f)
def _dummy(): pass

class Client:
    def __init__(self, port): self.port = port
    def call(self, m, p, body=None, h=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60); c.request(m, p, body, h or {}); r = c.getresponse(); d = r.read(); return r.status, d
    def j(self, m, p, body=None, h=None):
        s, d = self.call(m, p, body, h); return s, (json.loads(d) if d[:1] in b"{[" else d)

def _server(**kw):
    srv = make_server(port=0, work_dir=tempfile.mkdtemp(), **kw); threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, Client(srv.server_address[1])

def _wait(cl, jid, t=60):
    for _ in range(int(t / 0.2)):
        s, j = cl.j("GET", f"/api/jobs/{jid}")
        if j["status"] != "processing": return j
        time.sleep(0.2)
    raise AssertionError("timeout")

SRV, CL = _server()

def _upload(name="a.png", data=None):
    return CL.j("POST", "/api/jobs", data if data is not None else open(SAMPLE, "rb").read(), {"X-Filename": name})

def _analyzed(**settings):
    s, j = _upload(); assert s == 201
    st, _ = CL.j("POST", f"/api/jobs/{j['job_id']}/analyze", json.dumps({"width_mm": 100, "height_mm": 100, "fabric": "T_SHIRT", **settings}).encode()); assert st == 202
    return _wait(CL, j["job_id"])

def test_full_lifecycle_upload_analyze_review_approve_export_verified():
    j = _analyzed(hoop="generic-130x180")
    assert j["status"] == "review" and j["validation"]["status"] in ("VALID", "WARNING") and len(j["objects"]) >= 4
    assert j["exports_available"]["dst"]["enabled"] is False and "approve" in j["exports_available"]["dst"]["disabled_reason"]
    jid = j["job_id"]
    s, e = CL.j("POST", f"/api/jobs/{jid}/export/dst"); assert s == 403 and e["error"] == "APPROVAL_REQUIRED"
    assert CL.j("POST", f"/api/jobs/{jid}/approve", json.dumps({"approved": True, "note": "ok"}).encode())[0] == 200
    s, e = CL.j("POST", f"/api/jobs/{jid}/export/dst"); assert s == 200 and e["export"]["verified"]
    s, blob = CL.call("GET", f"/api/jobs/{jid}/artifacts/exports/design.dst"); assert s == 200 and len(blob) > 1000
    for f in ("pes", "jef", "emb"):
        s, e = CL.j("POST", f"/api/jobs/{jid}/export/{f}"); assert s == 501 and "UNAVAILABLE" in e["error"]
    assert not os.path.exists(os.path.join(SRV.jobs.get(jid).root, "exports", "design.pes"))
    j = CL.j("GET", f"/api/jobs/{jid}")[1]
    assert j["exports_available"]["pes"]["enabled"] is False and j["exports_available"]["dst"]["enabled"] is True

def test_progress_stages_and_job_dir_layout():
    j = _analyzed()
    assert j["progress"] >= 90 and j["stage"] in ("APPROVAL", "COMPLETE")
    assert {"UPLOAD", "SEGMENT", "STITCH_GENERATION", "QC", "AUTO_REPAIR", "APPROVAL", "EXPORT", "COMPLETE"} <= set(j["stages"])
    root = SRV.jobs.get(j["job_id"]).root
    for d in ("original", "vision", "objects", "blueprint", "technical", "stitches", "simulation", "qc", "comparison", "exports", "logs"):
        assert os.path.isdir(os.path.join(root, d))
    assert any(a.startswith("vision/01_") for a in j["artifacts"]) and any(a.startswith("simulation/11_") for a in j["artifacts"])
    lines = [json.loads(l) for l in open(os.path.join(root, "logs", "job.jsonl"))]
    assert {"timestamp", "job_id", "stage", "action", "status", "message"} <= set(lines[0])

def test_apply_repair_resets_approval_and_exports():
    j = _analyzed(targets={"max_jumps": 0, "max_trims": 0})
    jid = j["job_id"]; CL.j("POST", f"/api/jobs/{jid}/approve", json.dumps({"approved": True}).encode())
    CL.j("POST", f"/api/jobs/{jid}/export/dst")
    ok = [p for p in j["repair_proposals"] if p["status"] == "PROPOSED"]
    if ok:
        s, r = CL.j("POST", f"/api/jobs/{jid}/repair", json.dumps({"apply": [ok[0]["name"]]}).encode())
        assert s == 200 and r["applied"] == [ok[0]["name"]] and r["job"]["approved"] is False and r["job"]["exports_done"] == {}
        assert CL.j("POST", f"/api/jobs/{jid}/export/dst")[0] == 403
    s, r = CL.j("POST", f"/api/jobs/{jid}/repair", json.dumps({"apply": ["no_such_repair"]}).encode()); assert s == 409

def test_corrupt_and_spoofed_uploads_rejected_and_never_analyzed():
    s, j = _upload("x.png", b"\x89PNG\r\n\x1a\nxx"); assert j["status"] == "failed" and "INPUT_REJECTED" in j["error"]
    assert CL.j("POST", f"/api/jobs/{j['job_id']}/analyze", b"{}")[0] == 409
    s, j = _upload("fake.png", b"hello this is not an image"); assert j["status"] == "failed"
    s, j = _upload("h.dst", open(DST, "rb").read()); assert j["input_kind"] == "dst"
    s, e = CL.j("POST", f"/api/jobs/{j['job_id']}/analyze", b"{}"); assert s == 422 and e["error"] == "INPUT_KIND_NOT_DIGITIZABLE"

def test_extension_spoof_detected_by_content():
    s, j = _upload("photo.jpg"); assert j["input_kind"] == "png" and any("extension" in w for w in j["warnings"])

def test_path_traversal_and_bad_ids_blocked():
    j = _analyzed(); jid = j["job_id"]
    for p in ("..%2F..%2Fetc%2Fpasswd", "vision/../../../etc/passwd", "%2Fetc%2Fpasswd"):
        assert CL.call("GET", f"/api/jobs/{jid}/artifacts/{p}")[0] in (400, 404)
    assert CL.call("GET", "/api/jobs/..%2F..%2Ftmp")[0] in (400, 404)
    s, _ = CL.call("POST", "/api/jobs", b"x", {"X-Filename": "../../../../tmp/pwn.png"})
    assert not os.path.exists("/tmp/pwn.png")

def test_upload_size_limit_enforced():
    srv, cl = _server(max_mb=1)
    s, e = cl.j("POST", "/api/jobs", b"0" * (1024 * 1024 + 10), {"X-Filename": "big.png"}); assert s == 413 and e["error"] == "FILE_TOO_LARGE"

def test_bad_settings_and_bad_json_are_400():
    s, j = _upload()
    assert CL.j("POST", f"/api/jobs/{j['job_id']}/analyze", json.dumps({"width_mm": -5, "height_mm": 10}).encode())[0] == 400
    assert CL.j("POST", f"/api/jobs/{j['job_id']}/analyze", b"{not json")[0] == 400

def test_non_loopback_requires_token_and_token_is_enforced():
    with pytest.raises(SystemExit):
        make_server(host="0.0.0.0", port=0, work_dir=tempfile.mkdtemp())
    srv, cl = _server(token="s3cret")
    assert cl.call("GET", "/api/capabilities")[0] == 401
    assert cl.call("GET", "/api/capabilities", None, {"X-Token": "s3cret"})[0] == 200
    assert cl.call("GET", "/")[0] == 200            # the static page itself carries no design data

def test_rate_limit():
    from embcopilot.web.server import RateLimiter
    rl = RateLimiter(5); assert [rl.ok("1.1.1.1") for _ in range(7)] == [True] * 5 + [False] * 2

def test_capabilities_endpoint_is_honest():
    s, c = CL.j("GET", "/api/capabilities")
    assert c["formats"]["pes"]["status"] == "UNAVAILABLE" and c["formats"]["exp"]["status"] == "EXPERIMENTAL"
    assert c["emb"]["state"] == "EMB_API_NOT_CONFIGURED" and c["wilcom"]["mock"] == "MOCK" and "UNAVAILABLE" in c["wilcom"]["es65"]
    assert any(m["name"] == "SAM 2" and m["state"] == "UNAVAILABLE" for m in c["models"])

def test_segment_endpoint_reports_sam2_unavailable_with_fallback():
    j = _analyzed(); s, e = CL.j("POST", f"/api/jobs/{j['job_id']}/segment", b"{}")
    assert s == 501 and e["error"] == "SAM2_UNAVAILABLE" and "Standard" in e["fallback"]

def test_command_endpoint_parses_but_never_executes():
    j = _analyzed(); s, r = CL.j("POST", f"/api/jobs/{j['job_id']}/command", json.dumps({"text": "reduce trims"}).encode())
    assert s == 200 and r["executed"] is False and r["requires_approval"] is True

def test_zip_download_contains_all_stage_folders():
    import io, zipfile
    j = _analyzed(); s, blob = CL.call("GET", f"/api/jobs/{j['job_id']}/zip")
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert s == 200 and any(n.startswith("vision/") for n in names) and any(n.startswith("stitches/") for n in names) and any(n.startswith("logs/") for n in names)

def test_delete_job_removes_files_and_cleanup_by_age():
    j = _analyzed(); root = SRV.jobs.get(j["job_id"]).root
    assert CL.j("DELETE", f"/api/jobs/{j['job_id']}")[0] == 200 and not os.path.exists(root)
    j2 = _analyzed(); SRV.jobs.get(j2["job_id"]).created -= 7200
    assert SRV.jobs.cleanup_older_than(1) >= 1

def test_svg_upload_through_the_dashboard_uses_vector_mode():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="80mm" height="80mm" viewBox="0 0 80 80"><circle cx="40" cy="40" r="25" fill="#c00"/></svg>'
    s, j = _upload("logo.svg", svg); assert j["input_kind"] == "svg"
    CL.j("POST", f"/api/jobs/{j['job_id']}/analyze", json.dumps({"width_mm": 80, "height_mm": 80}).encode())
    j = _wait(CL, j["job_id"]); assert j["status"] == "review" and j["vision_mode"].startswith("Vector")

def test_dashboard_html_served_and_javascript_parses():
    s, page = CL.call("GET", "/"); assert s == 200 and b"Drag" in page and b"Approve" in page
    js = re.search(rb"<script>(.*)</script>", page, re.S).group(1)
    p = os.path.join(tempfile.mkdtemp(), "ui.js"); open(p, "wb").write(js)
    try:
        r = subprocess.run(["node", "--check", p], capture_output=True, timeout=30)
    except FileNotFoundError:
        return
    assert r.returncode == 0, r.stderr.decode()

def test_ui_never_renders_download_for_unsupported_formats():
    page = CL.call("GET", "/")[1].decode()
    assert "${c.enabled?'':'disabled'}" in page and "c.disabled_reason" in page       # buttons come from server capability flags, not hard-coded
