"""
Mock Wilcom adapter.

A real, working in-memory simulation of the WilcomAdapter surface — not a
stub that returns fixed values. Tool selection, object creation, property
changes, and zoom all actually mutate an internal WilcomScreenState, so the
decision engine, command parser, and action executor can be exercised
end-to-end (including verifying results) without any real Wilcom
installation or file at risk. This is the intended default mode for
development and for `--simulate` (see cli — simulate/dry-run modes).
"""

from __future__ import annotations

from ..domain import ActionStatus
from .schema import ActionRequest, ActionResult
from .wilcom_adapter import WilcomAdapter, WilcomScreenState


class MockWilcomAdapter(WilcomAdapter):
    def __init__(self):
        self._state = WilcomScreenState(
            application_detected=True,
            window_title="Wilcom ES-65 (mock)",
            active_document="Untitled.emb",
            zoom=100.0,
            canvas_region=(0, 0, 1024, 768),
        )
        self._objects: dict[str, dict] = {}
        self._properties: dict = {}

    # ---- read-only ----------------------------------------------------

    def detect_application(self) -> bool:
        return self._state.application_detected

    def get_screen_state(self) -> WilcomScreenState:
        return self._state

    def read_properties(self) -> dict:
        return dict(self._properties)

    def verify_result(self, request: ActionRequest) -> bool:
        # The mock can always verify its own state, since it IS the state —
        # a real adapter would need an actual screenshot/UI read here, and
        # would sometimes have to report UNKNOWN instead of True/False.
        return True

    # ---- actions --------------------------------------------------------

    def focus_document(self) -> ActionResult:
        req = ActionRequest(action="focus_document", risk="safe", requires_confirmation=False)
        return ActionResult(req, ActionStatus.SUCCESS.value, "document focused", True)

    def zoom(self, level: float) -> ActionResult:
        self._state.zoom = level
        req = ActionRequest(action="zoom", params={"level": level}, risk="safe", requires_confirmation=False)
        return ActionResult(req, ActionStatus.SUCCESS.value, f"zoom set to {level}", True)

    def fit_design(self) -> ActionResult:
        self._state.zoom = 100.0
        req = ActionRequest(action="fit_design", risk="safe", requires_confirmation=False)
        return ActionResult(req, ActionStatus.SUCCESS.value, "fit to design", True)

    def select_tool(self, tool_name: str) -> ActionResult:
        self._state.selected_tool = tool_name
        req = ActionRequest(action="select_tool", target=tool_name, risk="low_risk", requires_confirmation=False)
        return ActionResult(req, ActionStatus.SUCCESS.value, f"tool '{tool_name}' selected", True)

    def select_object(self, object_id: str) -> ActionResult:
        req = ActionRequest(action="select_object", target=object_id, risk="low_risk", requires_confirmation=False)
        if object_id not in self._objects:
            return ActionResult(req, ActionStatus.FAILED.value, f"no such mock object: {object_id}", True)
        self._state.selected_object = object_id
        return ActionResult(req, ActionStatus.SUCCESS.value, f"object '{object_id}' selected", True)

    def open_properties(self) -> ActionResult:
        self._state.dialog_open = "properties"
        req = ActionRequest(action="open_properties", risk="safe", requires_confirmation=False)
        return ActionResult(req, ActionStatus.SUCCESS.value, "properties dialog open", True)

    def set_properties(self, properties: dict) -> ActionResult:
        self._properties.update(properties)
        if self._state.selected_object:
            self._objects.setdefault(self._state.selected_object, {}).update(properties)
        self._state.active_properties = dict(self._properties)
        req = ActionRequest(
            action="set_properties", params=properties, risk="medium_risk", requires_confirmation=True
        )
        return ActionResult(req, ActionStatus.SUCCESS.value, f"properties set: {properties}", True)

    def create_geometry(self, points_mm: list[tuple[float, float]]) -> ActionResult:
        req = ActionRequest(
            action="create_geometry",
            params={"point_count": len(points_mm)},
            risk="medium_risk",
            requires_confirmation=True,
        )
        if len(points_mm) < 2:
            return ActionResult(req, ActionStatus.FAILED.value, "need at least 2 points to create geometry", True)
        object_id = f"mock_obj_{len(self._objects) + 1}"
        self._objects[object_id] = {"points_mm": points_mm}
        self._state.selected_object = object_id
        return ActionResult(req, ActionStatus.SUCCESS.value, f"created '{object_id}' with {len(points_mm)} points", True)

    def execute_action(self, request: ActionRequest) -> ActionResult:
        """Generic dispatch for actions not covered by a dedicated method."""
        dispatch = {
            "focus_document": lambda: self.focus_document(),
            "fit_design": lambda: self.fit_design(),
            "select_tool": lambda: self.select_tool(request.target or ""),
            "select_object": lambda: self.select_object(request.target or ""),
            "open_properties": lambda: self.open_properties(),
            "set_properties": lambda: self.set_properties(request.params),
            "create_geometry": lambda: self.create_geometry(request.params.get("points_mm", [])),
            "zoom": lambda: self.zoom(request.params.get("level", 100.0)),
        }
        handler = dispatch.get(request.action)
        if handler is None:
            return ActionResult(
                request, ActionStatus.UNKNOWN.value,
                f"mock adapter has no handler for action '{request.action}'", False,
            )
        return handler()
