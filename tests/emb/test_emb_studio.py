import csv, io, json, os, tempfile
from embcopilot.emb_studio import emb_capabilities, inspect_emb_bytes, handoff_bundle, build_object_manifest, MAX_EMB_BYTES
from embcopilot.vectorbadge import build_vector_badge
from embcopilot.ai.session import DesignSession

def test_six_separate_capabilities_and_honest_states():
    c = emb_capabilities()
    for k in ("EMB_IMPORT", "EMB_INSPECTION", "EMB_EDIT", "EMB_CREATE", "EMB_SAVE", "EMB_EXPORT"):
        assert k in c
    assert c["EMB_INSPECTION"]["state"] == "PARTIAL" and c["EMB_IMPORT"]["state"] == "UNAVAILABLE"
    assert all(c[k]["state"] == "REQUIRES_WILCOM_CONFIGURATION" for k in ("EMB_EDIT", "EMB_CREATE", "EMB_SAVE", "EMB_EXPORT"))
    assert "requires a configured supported Wilcom" in c["message"] and c["ui_button"]["enabled"] is False

def test_configured_but_unimplemented_backend_is_reported_as_such():
    os.environ["EMBCOPILOT_WILCOM_API_URL"] = "x"; os.environ["EMBCOPILOT_WILCOM_API_KEY"] = "y"
    try:
        from embcopilot.nativeemb import NativeEMBAdapter
        c = emb_capabilities(NativeEMBAdapter().detect_capability())
        assert c["EMB_CREATE"]["state"] == "CONFIGURED_PROTOCOL_NOT_IMPLEMENTED"
    finally:
        os.environ.pop("EMBCOPILOT_WILCOM_API_URL"); os.environ.pop("EMBCOPILOT_WILCOM_API_KEY")

def test_inspect_rejects_renamed_formats_and_empty():
    assert inspect_emb_bytes(b"LA:design  \r" + b"\0" * 600, "a.emb")["classification"] == "RENAMED_DST"
    assert inspect_emb_bytes(b"#PES0001" + b"\0" * 100, "a.emb")["classification"] == "RENAMED_PES"
    assert inspect_emb_bytes(b"", "a.emb")["classification"] == "EMPTY"
    assert inspect_emb_bytes(b"x" * 10, "notemb.dst")["warnings"][0] == "EXTENSION_NOT_EMB"

def test_inspect_never_claims_object_data_or_native_confirmation():
    r = inspect_emb_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"Some Design Name\0" * 10 + os.urandom(64), "real.emb")
    assert r["classification"] == "OLE_COMPOUND_FILE" and r["native_emb_confirmed"] is False
    assert r["object_level_data"].startswith("NOT_READABLE") and r["sha256"] and r["strings_undecoded"]
    assert any("Export a DST" in s or "export a DST" in s for s in r["next_steps"])

def test_inspect_is_bounded_and_handles_binary_noise():
    r = inspect_emb_bytes(os.urandom(200_000), "r.emb")
    assert r["ok"] and r["entropy_bits_per_byte"] > 7.5 and len(r["strings_undecoded"]) <= 30
    big = inspect_emb_bytes(b"\0" * (MAX_EMB_BYTES + 1), "big.emb")
    assert big["classification"] == "TOO_LARGE" and big["ok"] is False

def test_handoff_bundle_contents_and_no_emb_file():
    bp, deps, _ = build_vector_badge(); S = DesignSession(bp, deps, "denim")
    with tempfile.TemporaryDirectory() as d:
        r = handoff_bundle(S.build, d, "badge")
        assert r["not_an_emb"] and r["dst"]["status"] == "EXPORTED_VERIFIED"
        names = set(os.listdir(d))
        assert {"badge.dst", "design_objects.svg", "object_manifest.csv", "object_manifest.json", "HANDOFF_README.md"} <= names
        assert not any(n.lower().endswith(".emb") for n in names)
        rows = list(csv.DictReader(open(os.path.join(d, "object_manifest.csv"))))
        assert len(rows) == len(S.build.objects) and rows[0]["object_id"] == "outer_border" and rows[0]["stitch_type"] == "satin"
        assert float(rows[0]["pull_compensation_mm"]) >= 0 and rows[0]["underlay"]
        svg = open(os.path.join(d, "design_objects.svg")).read()
        assert 'fill-rule="evenodd"' in svg and 'id="outer_border"' in svg
        assert "NOT an EMB file" in open(os.path.join(d, "HANDOFF_README.md")).read()

def test_manifest_matches_plan_entry_exit_and_sequence():
    bp, deps, _ = build_vector_badge(); S = DesignSession(bp, deps)
    rows = build_object_manifest(S.build)
    seqs = [r["sequence"] for r in rows if r["sequence"]]
    assert seqs == sorted(seqs) and rows[0]["entry_mm"] and rows[0]["exit_mm"]
    o_rows = [r for r in rows if r["object_id"].endswith("_O")]
    assert o_rows and all(r["holes"] == 1 for r in o_rows)           # letter O keeps its counter in the handoff geometry

def test_handoff_blocked_when_qc_errors_exist():
    bp, deps, _ = build_vector_badge(); S = DesignSession(bp, deps)
    from embcopilot.qc_v2 import QCIssue, QCReport
    S.build.qc = QCReport([QCIssue("CENTER_CROSSING_TRAVEL", "ERROR", "Pathing QC", "forced")], {}, "generic_flat")
    with tempfile.TemporaryDirectory() as d:
        r = handoff_bundle(S.build, d, "x")
        assert r["dst"]["status"] == "BLOCKED_BY_QC" and not os.path.exists(os.path.join(d, "x.dst"))
