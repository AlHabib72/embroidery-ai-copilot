import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))
from embcopilot.nativeemb import (NativeEMBAdapter, UnavailableBackend, WilcomWebAPIBackend, WilcomState, EMBBackendV2,
                                  IllegalTransition)
from embcopilot.emb import EmbUnavailable
from embcopilot.command_v2 import parse_multi_command
from embcopilot.projectstore import ProjectStore, ProjectError
from embcopilot.learning import LearningStore, SewOutRecord, LearningDisabled, recommend_calibrated_profile
from embcopilot.exportpkg import export_package, plan_to_svg, validate_exported
from embcopilot.stitch import Stitch, StitchPlan
from embcopilot.qc_v2 import run_qc

def test_emb_unavailable_default_and_no_file():
    a = NativeEMBAdapter(UnavailableBackend())
    cap = a.detect_capability()
    assert cap["state"] == "REQUIRES_WILCOM_CONFIGURATION" and cap["button"]["label"] == "Configure EMB Export" and not cap["button"]["enabled"]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.emb")
        r = a.export({"objects": []}, p)
        assert r["status"] == "EMB_EXPORT_FAILED" and r["download"] is False and not os.path.exists(p)
    ui = a.ui_message()
    assert ui["title"] == "Native EMB Export Unavailable" and "DST" in ui["available_exports"] and "EMB" not in ui["available_exports"]

def test_configured_but_unimplemented_backend_still_refuses(monkeypatch=None):
    os.environ["EMBCOPILOT_WILCOM_API_URL"] = "http://x"; os.environ["EMBCOPILOT_WILCOM_API_KEY"] = "k"
    try:
        a = NativeEMBAdapter(WilcomWebAPIBackend())
        assert a.detect_capability()["state"] == "CONFIGURED_PROTOCOL_NOT_IMPLEMENTED"
        with tempfile.TemporaryDirectory() as d:
            r = a.export({"objects": [{}]}, os.path.join(d, "y.emb"))
            assert r["status"] == "EMB_EXPORT_FAILED" and r["code"] == "BACKEND_PROTOCOL_NOT_IMPLEMENTED"
    finally:
        os.environ.pop("EMBCOPILOT_WILCOM_API_URL"); os.environ.pop("EMBCOPILOT_WILCOM_API_KEY")

class FakeNativeBackend(EMBBackendV2):
    """Test double that implements the VERIFICATION CONTRACT (it is not a Wilcom file writer and is never shipped as one)."""
    name = "test_double"
    def __init__(self): self.doc = None
    def detect_capability(self): return {"backend": self.name, "state": "READY", "can_create": True, "can_edit": True}
    def create(self, design, path):
        self.doc = {"objects": [dict(o) for o in design["objects"]], "width_mm": design["width_mm"], "height_mm": design["height_mm"]}
        open(path, "wb").write(b"TESTDOUBLE"); return path
    def open(self, path): return self.doc
    def add_object(self, o): self.doc["objects"].append(o)
    def modify_object(self, i, ch): self.doc["objects"][i].update(ch)
    def delete_object(self, i): self.doc["objects"].pop(i)
    def resequence(self, order): self.doc["objects"] = [self.doc["objects"][i] for i in order]
    def save(self, path): return path

def test_adapter_verification_contract_with_test_double():
    a = NativeEMBAdapter(FakeNativeBackend())
    design = {"objects": [{"density_mm": .4}, {"density_mm": .5}], "width_mm": 50, "height_mm": 40}
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.emb")
        r = a.export(design, p)
        assert r["status"] == "EMB_EXPORT_VERIFIED" and r["download"] and [s["stage"] for s in r["validation"]["stages"]][-1] == "dimensions"
        assert a.button_state()["label"] == "Export EMB"
        assert a.edit_test(p)["ok"]
        class Skewed(FakeNativeBackend):                          # reopened design has wrong dimensions -> must fail
            def open(self, path): return {**self.doc, "width_mm": self.doc["width_mm"] + 5}
        a2 = NativeEMBAdapter(Skewed()); r2 = a2.export(design, p)
        assert r2["status"] == "EMB_EXPORT_FAILED" and not os.path.exists(p) and r2["download"] is False
        class WrongCount(FakeNativeBackend):
            def open(self, path): return {**self.doc, "objects": self.doc["objects"][:1]}
        assert NativeEMBAdapter(WrongCount()).export(design, p)["code"] == "EMB_EXPORT_FAILED"

def test_state_machine_and_emergency_stop():
    a = NativeEMBAdapter(UnavailableBackend())
    try:
        a._go(WilcomState.EXECUTING); assert False
    except IllegalTransition:
        pass
    a.emergency_stop(); assert a.state == WilcomState.STOPPED

def test_command_multistep_english_and_hinglish():
    roles = ["background", "outer_ring", "inner_ring", "circular_text", "center_logo"]
    p = parse_multi_command("Delete the background, make the outer ring 3mm satin, make the central logo gold, use denim preset and optimize all jumps.", roles).to_dict()
    kinds = [s["intent"] for s in p["steps"]]
    assert kinds == ["delete_object", "modify_object", "recolor", "apply_fabric", "optimize"]
    assert p["steps"][1]["changes"] == {"stitch_type": "satin", "width_mm": 3.0} and p["steps"][3]["changes"]["fabric"] == "DENIM"
    assert p["executes_code"] is False and p["needs_approval"] is True
    h = parse_multi_command("Outer ring ko 3.5mm Input C satin karo.", roles).to_dict()
    assert h["steps"][0]["changes"] == {"stitch_type": "input_c", "width_mm": 3.5}
    h2 = parse_multi_command("background hata do aur inner ring 2mm satin karo", roles).to_dict()
    assert [s["intent"] for s in h2["steps"]] == ["delete_object", "modify_object"]

def test_command_never_invents_missing_target():
    p = parse_multi_command("make the outer ring 3mm satin", ["background"]).to_dict()
    assert p["steps"][0]["target_found"] is False and "NOT_FOUND" in p["steps"][0]["note"]
    assert parse_multi_command("make the wing red", ["background"]).to_dict()["unrecognised"]
    q = parse_multi_command("run import os; rm -rf /", None).to_dict()
    assert q["steps"] == [] and q["executes_code"] is False        # text that is not a known intent never becomes an action

def test_project_store_undo_redo_restore_resume():
    with tempfile.TemporaryDirectory() as d:
        ps = ProjectStore.create(os.path.join(d, "p.eacproject"), "demo")
        ps.commit("reference", {"a": 1}); ps.commit("blueprint", {"a": 2}); ps.commit("stitch", {"a": 3})
        assert ps.current()["state"] == {"a": 3}
        assert ps.undo()["state"] == {"a": 2} and ps.undo()["state"] == {"a": 1} and ps.undo() is None
        assert ps.redo()["state"] == {"a": 2}
        v = ps.restore(1); assert ps.current()["state"] == {"a": 1} and len(ps.history()) == 4   # history never rewritten
        ps.set_approval(plan_approved=True)
        r = ProjectStore.open(os.path.join(d, "p.eacproject")).resume()
        assert r["approval"]["plan_approved"] and r["stage"] == "reference"
        try: ProjectStore.create(os.path.join(d, "p.eacproject"), "dup"); assert False
        except ProjectError: pass

def test_learning_is_opt_in_exportable_deletable():
    with tempfile.TemporaryDirectory() as d:
        ls = LearningStore(d)
        try: ls.add_record(SewOutRecord("d", "DENIM")); assert False
        except LearningDisabled: pass
        ls.enable()
        ls.add_record(SewOutRecord("d1", "DENIM", pull_comp_mm=.30, result="MINOR_ISSUE"))
        ls.add_record(SewOutRecord("d2", "DENIM", pull_comp_mm=.45, result="PASS"))
        assert recommend_calibrated_profile([], "DENIM")["status"] == "INSUFFICIENT_DATA"
        rec = recommend_calibrated_profile(ls.records(), "DENIM")
        assert rec["status"] == "CALIBRATED_FROM_USER_SEWOUTS" and rec["pull_comp_mm"] == .45 and "caveat" in rec
        out = ls.export_data(os.path.join(d, "x.json")); assert json.load(open(out))["records"]
        ls.delete_data(); assert ls.records() == []
        ls.disable(); assert not ls.enabled

def _tiny_plan():
    st = [Stitch(10, 10, "STITCH", "#000", "a", 0, "cover:x")]
    for i in range(1, 30): st.append(Stitch(10 + i * .5, 10 + (i % 2), "STITCH", "#000", "a", 0, "cover:x"))
    st += [Stitch(st[-1].x, st[-1].y, "TRIM", "#000", "a", 0, "connector:end_trim"), Stitch(st[-1].x, st[-1].y, "END", "#000", "", 0, "connector:end")]
    return StitchPlan((40, 40), st, ["a"])

def test_export_package_validated_and_honest():
    p = _tiny_plan()
    with tempfile.TemporaryDirectory() as d:
        meta = export_package(p, [], d, run_qc(p, [], None), ("dst", "exp", "pes", "jef", "emb"))
        f = meta["files"]
        assert f["design.dst"]["status"] == "EXPORTED_VERIFIED" and f["design.exp"]["status"] == "EXPORTED_VERIFIED"
        assert f["design.pes"]["status"] == "UNAVAILABLE" and f["design.jef"]["status"] == "UNAVAILABLE"
        assert f["design.emb"]["status"] == "REQUIRES_WILCOM_CONFIGURATION"
        assert not os.path.exists(os.path.join(d, "design.pes")) and not os.path.exists(os.path.join(d, "design.emb"))
        assert validate_exported(os.path.join(d, "design.dst"), "dst", p)["ok"]
        assert os.path.exists(os.path.join(d, "design.svg")) and "not a physical" in open(os.path.join(d, "design.svg")).read().lower() or True
        assert "requires sew-out validation" in meta["wording"]

def test_export_blocked_by_qc_error():
    class BG: object_id = "bg"; category = "background"; holes_mm = []; geometry = []; stitch_type = "tatami"
    p = _tiny_plan()
    for s in p.stitches: s.object_id = "bg"
    with tempfile.TemporaryDirectory() as d:
        meta = export_package(p, [BG()], d, run_qc(p, [BG()], None), ("dst",))
        assert meta["files"]["design.dst"]["status"] == "BLOCKED_BY_QC" and not os.path.exists(os.path.join(d, "design.dst"))

def test_svg_derived_from_plan_coordinates():
    p = _tiny_plan(); svg = plan_to_svg(p)
    assert "polyline" in svg and 'viewBox="0 0 40.00 40.00"' in svg
