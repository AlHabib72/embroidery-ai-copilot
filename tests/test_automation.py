"""Tests for embcopilot.automation — mock adapter, executor, file adapters."""

import os
import tempfile

import pytest

from embcopilot.automation.emb_adapter import DSTAdapter, EMBAdapter, JEFAdapter, adapter_for
from embcopilot.automation.executor import ActionExecutor, EmergencyStopActive
from embcopilot.automation.mock_adapter import MockWilcomAdapter
from embcopilot.automation.schema import ActionRequest, AutomationPolicy
from embcopilot.automation.wilcom_adapter import WilcomES65Adapter
from embcopilot.domain import ActionStatus
from embcopilot.vision import NotImplementedCapability


# ----------------------------------------------------------------------
# MockWilcomAdapter
# ----------------------------------------------------------------------


def test_mock_adapter_detects_application():
    adapter = MockWilcomAdapter()
    assert adapter.detect_application() is True


def test_mock_adapter_select_tool_updates_screen_state():
    adapter = MockWilcomAdapter()
    result = adapter.select_tool("satin")
    assert result.status == ActionStatus.SUCCESS.value
    assert adapter.get_screen_state().selected_tool == "satin"


def test_mock_adapter_create_then_select_object():
    adapter = MockWilcomAdapter()
    create = adapter.create_geometry([(0.0, 0.0), (1.0, 1.0), (2.0, 0.0)])
    assert create.status == ActionStatus.SUCCESS.value
    obj_id = adapter.get_screen_state().selected_object
    result = adapter.select_object(obj_id)
    assert result.status == ActionStatus.SUCCESS.value


def test_mock_adapter_select_missing_object_fails_honestly():
    adapter = MockWilcomAdapter()
    result = adapter.select_object("does_not_exist")
    assert result.status == ActionStatus.FAILED.value


def test_mock_adapter_create_geometry_needs_at_least_two_points():
    adapter = MockWilcomAdapter()
    result = adapter.create_geometry([(0.0, 0.0)])
    assert result.status == ActionStatus.FAILED.value


def test_mock_adapter_verify_result_is_always_true_by_design():
    adapter = MockWilcomAdapter()
    req = ActionRequest(action="select_tool", target="satin")
    assert adapter.verify_result(req) is True


# ----------------------------------------------------------------------
# WilcomES65Adapter — must never fake a capability
# ----------------------------------------------------------------------


def test_real_wilcom_adapter_raises_not_implemented_for_every_capability():
    adapter = WilcomES65Adapter()
    with pytest.raises(NotImplementedCapability):
        adapter.detect_application()
    with pytest.raises(NotImplementedCapability):
        adapter.select_tool("satin")
    with pytest.raises(NotImplementedCapability):
        adapter.create_geometry([(0.0, 0.0), (1.0, 1.0)])


# ----------------------------------------------------------------------
# ActionExecutor
# ----------------------------------------------------------------------


def test_executor_runs_safe_action_unattended():
    executor = ActionExecutor(MockWilcomAdapter())
    req = ActionRequest(action="focus_document", risk="safe", requires_confirmation=False)
    result = executor.run(req)
    assert result.status == ActionStatus.SUCCESS.value


def test_executor_holds_unapproved_medium_risk_action_as_planned():
    executor = ActionExecutor(MockWilcomAdapter())
    req = ActionRequest(action="set_properties", params={"density": 0.4}, risk="medium_risk", requires_confirmation=True)
    result = executor.run(req, approved=False)
    assert result.status == ActionStatus.PLANNED.value  # never executed, never faked as success


def test_executor_runs_action_once_approved():
    executor = ActionExecutor(MockWilcomAdapter())
    req = ActionRequest(action="set_properties", params={"density": 0.4}, risk="medium_risk", requires_confirmation=True)
    result = executor.run(req, approved=True)
    assert result.status == ActionStatus.SUCCESS.value


def test_executor_logs_every_action():
    executor = ActionExecutor(MockWilcomAdapter())
    executor.run(ActionRequest(action="focus_document", risk="safe", requires_confirmation=False))
    executor.run(ActionRequest(action="fit_design", risk="safe", requires_confirmation=False))
    assert len(executor.log) == 2
    assert all("timestamp" in entry.to_dict() for entry in executor.log)


def test_executor_stops_after_a_failure_and_refuses_further_actions():
    executor = ActionExecutor(MockWilcomAdapter())
    fail_req = ActionRequest(action="select_object", target="ghost", risk="low_risk", requires_confirmation=False)
    result = executor.run(fail_req, approved=True)
    assert result.status == ActionStatus.FAILED.value
    assert executor.stopped is True
    with pytest.raises(EmergencyStopActive):
        executor.run(ActionRequest(action="focus_document", risk="safe", requires_confirmation=False))


def test_executor_resumes_after_reset():
    executor = ActionExecutor(MockWilcomAdapter())
    executor.run(
        ActionRequest(action="select_object", target="ghost", risk="low_risk", requires_confirmation=False),
        approved=True,
    )
    assert executor.stopped is True
    executor.reset()
    result = executor.run(ActionRequest(action="focus_document", risk="safe", requires_confirmation=False))
    assert result.status == ActionStatus.SUCCESS.value


def test_automation_policy_rejects_unknown_risk_level():
    with pytest.raises(ValueError):
        AutomationPolicy(allow_unattended_up_to="not_a_real_level")


def test_automation_policy_default_only_allows_safe_unattended():
    policy = AutomationPolicy()
    safe_req = ActionRequest(action="x", risk="safe", requires_confirmation=False)
    risky_req = ActionRequest(action="x", risk="high_risk", requires_confirmation=False)
    assert policy.may_run_unattended(safe_req) is True
    assert policy.may_run_unattended(risky_req) is False


# ----------------------------------------------------------------------
# File adapters (DST / EMB / JEF)
# ----------------------------------------------------------------------


def test_dst_adapter_full_capabilities():
    caps = DSTAdapter().capabilities().to_dict()
    assert caps["read_stitches"] == "supported"
    assert caps["write_stitches"] == "supported"


def test_emb_adapter_never_claims_read_stitches():
    caps = EMBAdapter().capabilities().to_dict()
    assert caps["read_stitches"] == "unavailable"
    assert caps["detect"] == "supported"


def test_emb_adapter_metadata_is_undecoded_and_honest():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "design.emb")
        content = b"\x00\x01\x02\x03fake emb content"
        with open(path, "wb") as fh:
            fh.write(content)
        meta = EMBAdapter().metadata(path)
        assert meta["file_size_bytes"] == os.path.getsize(path)
        assert "not available" in meta["note"].lower()
        assert meta["first_16_bytes_hex"] == content[:16].hex()


def test_jef_adapter_raises_not_implemented():
    with pytest.raises(NotImplementedCapability):
        JEFAdapter().metadata("whatever.jef")


def test_adapter_for_picks_the_right_adapter_by_extension():
    assert isinstance(adapter_for("x.dst"), DSTAdapter)
    assert isinstance(adapter_for("x.emb"), EMBAdapter)
    assert adapter_for("x.unknownformat") is None
