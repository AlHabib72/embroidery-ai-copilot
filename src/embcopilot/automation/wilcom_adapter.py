"""
Wilcom automation adapter interface.

No official Wilcom API exists for ES-65 (see this project's very first
conversation). Every capability below is therefore either:
  - not implemented at all (raises NotImplementedCapability), or
  - implemented only by MockWilcomAdapter (see mock_adapter.py), which
    simulates state transitions in memory for testing the decision engine
    without touching a real Wilcom installation.

WilcomES65Adapter exists so the rest of the system (ActionExecutor,
CopilotSession) has a concrete class to hold, but every method raises
NotImplementedCapability. This is intentional — see project brief section
68: "If a capability cannot currently be implemented reliably, raise
NotImplementedCapability. Do not fake it." Building real screen automation
means UI-element detection and/or accessibility APIs (section 23), which
this codebase does not yet implement; wiring that up without those pieces
would be exactly the "fake it" this project explicitly forbids.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..vision import NotImplementedCapability
from .schema import ActionRequest, ActionResult


@dataclass
class WilcomScreenState:
    application_detected: bool = False
    window_title: str | None = None
    active_document: str | None = None
    zoom: float | None = None
    canvas_region: tuple[int, int, int, int] | None = None
    selected_tool: str | None = None
    selected_object: str | None = None
    dialog_open: str | None = None
    active_properties: dict = field(default_factory=dict)
    cursor_position: tuple[int, int] | None = None


class WilcomAdapter(ABC):
    """Capability surface every Wilcom-version adapter should expose.

    Section 23's hierarchy applies to any real implementation: prefer an
    official API/supported integration, then accessibility/UI automation,
    then keyboard shortcuts, then UI element detection, and only as a last
    resort raw coordinate-based mouse control. None of that hierarchy is
    implemented yet — see the class docstring.
    """

    @abstractmethod
    def detect_application(self) -> bool: ...

    @abstractmethod
    def get_screen_state(self) -> WilcomScreenState: ...

    @abstractmethod
    def focus_document(self) -> ActionResult: ...

    @abstractmethod
    def zoom(self, level: float) -> ActionResult: ...

    @abstractmethod
    def fit_design(self) -> ActionResult: ...

    @abstractmethod
    def select_tool(self, tool_name: str) -> ActionResult: ...

    @abstractmethod
    def select_object(self, object_id: str) -> ActionResult: ...

    @abstractmethod
    def open_properties(self) -> ActionResult: ...

    @abstractmethod
    def read_properties(self) -> dict: ...

    @abstractmethod
    def set_properties(self, properties: dict) -> ActionResult: ...

    @abstractmethod
    def create_geometry(self, points_mm: list[tuple[float, float]]) -> ActionResult: ...

    @abstractmethod
    def execute_action(self, request: ActionRequest) -> ActionResult: ...

    @abstractmethod
    def verify_result(self, request: ActionRequest) -> bool: ...


class WilcomES65Adapter(WilcomAdapter):
    """Real ES-65 adapter placeholder. Every method raises
    NotImplementedCapability — no capability here is faked.

    This class exists to document the intended surface precisely, and so
    ActionExecutor and CopilotSession have a real (non-mock) type to accept
    once any of these capabilities becomes genuinely implementable through
    a legitimate integration path (official API, accessibility API, or a
    supported plugin/macro interface) — see README "EMB / Wilcom support".
    """

    _MSG = (
        "{name}() is not implemented for the real Wilcom ES-65 adapter in "
        "this build. No official Wilcom automation API is used here — see "
        "README 'Wilcom support' for what a real implementation would "
        "require. Use MockWilcomAdapter for development/testing."
    )

    def _unavailable(self, name: str):
        raise NotImplementedCapability(self._MSG.format(name=name))

    def detect_application(self) -> bool:
        self._unavailable("detect_application")

    def get_screen_state(self) -> WilcomScreenState:
        self._unavailable("get_screen_state")

    def focus_document(self) -> ActionResult:
        self._unavailable("focus_document")

    def zoom(self, level: float) -> ActionResult:
        self._unavailable("zoom")

    def fit_design(self) -> ActionResult:
        self._unavailable("fit_design")

    def select_tool(self, tool_name: str) -> ActionResult:
        self._unavailable("select_tool")

    def select_object(self, object_id: str) -> ActionResult:
        self._unavailable("select_object")

    def open_properties(self) -> ActionResult:
        self._unavailable("open_properties")

    def read_properties(self) -> dict:
        self._unavailable("read_properties")

    def set_properties(self, properties: dict) -> ActionResult:
        self._unavailable("set_properties")

    def create_geometry(self, points_mm: list[tuple[float, float]]) -> ActionResult:
        self._unavailable("create_geometry")

    def execute_action(self, request: ActionRequest) -> ActionResult:
        self._unavailable("execute_action")

    def verify_result(self, request: ActionRequest) -> bool:
        self._unavailable("verify_result")
