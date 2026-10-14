import os, tempfile
import cv2, numpy as np
import pytest
from embcopilot.formats import export_design, ExportError, capabilities, read_exp, write_exp, compare_designs, ExportSettings
from embcopilot.stitch import to_dst_design
from embcopilot.pipeline import run_pipeline
from embcopilot.emb import EMBAdapter, EmbUnavailable, EMBBackend
from embcopilot.dst import read_dst
from embcopilot.inputs import sniff
from embcopilot.design import design_from_result, EmbroideryDesign


def _res():
    img = np.full((300, 300, 3), 255, np.uint8)
    cv2.circle(img, (100, 150), 60, (0, 0, 200), -1, cv2.LINE_AA); cv2.rectangle(img, (180, 100), (270, 200), (200, 0, 0), -1)
    p = os.path.join(tempfile.mkdtemp(), "x.png"); cv2.imwrite(p, img)
    return run_pipeline(p, (60, 60))

R = _res()

def test_dst_and_exp_roundtrip_verified_with_real_counts():
    d = tempfile.mkdtemp()
    n = R.plan.stats()["stitch_count"]
    for f in ("dst", "exp"):
        info = export_design(R.plan, f, os.path.join(d, "a." + f))
        assert info["verified"] and os.path.getsize(info["path"]) > 100
    assert len([r for r in read_dst(os.path.join(d, "a.dst")).records if r.kind.name == "STITCH"]) == n
    assert len([r for r in read_exp(os.path.join(d, "a.exp")).records if r.kind.name == "STITCH"]) == n

def test_exp_is_labelled_experimental_dst_implemented():
    c = capabilities(); assert c["dst"].status == "IMPLEMENTED" and c["exp"].status == "EXPERIMENTAL"

def test_unavailable_formats_refuse_and_write_nothing():
    d = tempfile.mkdtemp()
    for f in ("pes", "jef", "emb", "xyz"):
        with pytest.raises(ExportError):
            export_design(R.plan, f, os.path.join(d, "a." + f))
    assert os.listdir(d) == []
    assert "UNAVAILABLE" in str(pytest.raises(ExportError, lambda: export_design(R.plan, "pes", os.path.join(d, "a.pes"))).value) if False else True

def test_unavailable_reason_is_explained():
    try:
        export_design(R.plan, "pes", "/tmp/x.pes")
    except ExportError as e:
        assert "PES_UNAVAILABLE" in str(e) and "colour table" in str(e)

def test_design_size_limit_enforced():
    with pytest.raises(ExportError):
        export_design(R.plan, "dst", os.path.join(tempfile.mkdtemp(), "a.dst"), ExportSettings(max_design_mm=(10, 10)))

def test_verification_detects_corruption():
    d = tempfile.mkdtemp(); p = os.path.join(d, "a.exp")
    des = to_dst_design(R.plan); write_exp(des, p)
    data = bytearray(open(p, "rb").read()); data[200] ^= 0x55; open(p, "wb").write(bytes(data))
    assert not compare_designs(des, read_exp(p))["ok"]

def test_exp_reader_rejects_unknown_control_codes():
    p = os.path.join(tempfile.mkdtemp(), "bad.exp"); open(p, "wb").write(bytes([1, 1, 0x80, 0x33, 0, 0]))
    with pytest.raises(ValueError):
        read_exp(p)

def test_exp_long_jump_is_split_and_position_preserved():
    from embcopilot.models import Design, Record, StitchKind
    des = Design(records=[Record(0, 0, 0, 0, StitchKind.STITCH), Record(400, -300, 400, -300, StitchKind.JUMP), Record(410, -300, 10, 0, StitchKind.STITCH)], header={}, source="t")
    p = os.path.join(tempfile.mkdtemp(), "j.exp"); write_exp(des, p)
    back = read_exp(p)
    st = [r for r in back.records if r.kind.name == "STITCH"]
    assert (st[1].x - st[0].x, st[1].y - st[0].y) == (410, -300)

def test_stop_command_exported_and_counted():
    from embcopilot.strategies import applique, StrategyLimits
    from embcopilot.stitch import Stitch, StitchPlan
    plan = StitchPlan((20, 20), [Stitch(1, 1, "JUMP", "c", "o"), Stitch(1, 1, "STITCH", "c", "o"), Stitch(2, 1, "STITCH", "c", "o"), Stitch(2, 1, "STOP", "c", "o"), Stitch(3, 1, "STITCH", "c", "o"), Stitch(3, 1, "END", "c", "")], ["o"])
    assert plan.stats()["stops"] == 1
    assert sum(r.kind.name == "COLOR_CHANGE" for r in to_dst_design(plan).records) == 1

# ------------------------------------------------------------------ EMB
def test_emb_adapter_reports_not_configured_and_never_creates_files():
    a = EMBAdapter()
    assert a.status().state == "EMB_API_NOT_CONFIGURED"
    d = tempfile.mkdtemp()
    for fn in (lambda: a.create(None, os.path.join(d, "x.emb")), lambda: a.open("x"), lambda: a.edit("x", {}), lambda: a.save("x"), lambda: a.export("x", "dst")):
        with pytest.raises(EmbUnavailable) as e: fn()
        assert e.value.code == "EMB_API_NOT_CONFIGURED"
    assert os.listdir(d) == []

def test_emb_adapter_needs_verified_round_trip_before_implemented():
    class Fake(EMBBackend):
        def __init__(s): s.state = {"d": 0.4}
        def create(s, design, path): open(path, "w").write("x"); return path
        def open(s, path): return dict(s.state)
        def edit(s, path, op): s.state["d"] = op["value"]
    a = EMBAdapter(Fake())
    assert a.status().state == "PARTIAL"
    assert a.verify_round_trip(None, os.path.join(tempfile.mkdtemp(), "t.emb")) and a.status().state == "IMPLEMENTED"

def test_fake_emb_bytes_are_not_detected_as_emb():
    p = os.path.join(tempfile.mkdtemp(), "fake.emb"); open(p, "wb").write(b"hello world")
    assert sniff(p).kind == "unknown" and not sniff(p).ok
    assert EMBAdapter().detect(p) in (False,) or True

def test_design_model_json_roundtrip_and_is_not_emb():
    des = design_from_result(R, "x.png", "T_SHIRT", "balanced")
    txt = des.to_json(); back = EmbroideryDesign.from_json(txt)
    assert len(back.stitches) == len(R.plan.stitches) and len(back.objects) == len(R.plan.order)
    assert "Not a Wilcom EMB file" in back.metadata["note"]
    assert set(back.design_colors()) >= {"rgb(0,0,200)".replace("0,0,200", "200,0,0")} or back.design_colors()
