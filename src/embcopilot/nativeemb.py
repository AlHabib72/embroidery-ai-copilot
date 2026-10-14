"""NativeEMBAdapter v2 (v0.6) - honest, backend-based, state-machine driven.

Native .EMB is Wilcom's proprietary container.  This project does NOT write it, rename another format to it, or
reverse-engineer it.  A file named design.emb is produced ONLY by a real, supported Wilcom workflow behind a
backend that has passed ``validate()`` (generate -> save -> exists -> reopen -> object count -> dimensions).

Backends
  UnavailableBackend         default; always reports why
  WilcomWebAPIBackend        needs a licensed/supported Wilcom web/API endpoint (EMBCOPILOT_WILCOM_API_URL + _KEY).
                             The protocol is NOT implemented here (no public spec was available): configured => state
                             CONNECTING and every call raises EmbUnavailable("BACKEND_PROTOCOL_NOT_IMPLEMENTED").
  WilcomEWABackend           supported Wilcom SDK/capability route (EMBCOPILOT_WILCOM_SDK) - same honesty rule
  WilcomDesktopBridge        locally installed Wilcom on Windows (EMBCOPILOT_WILCOM_EXE) - same honesty rule

Implementing a real backend = subclass ``EMBBackendV2`` and pass it to ``NativeEMBAdapter``.  The adapter's
``validate`` then enforces the verification contract; the UI button state is derived from ``button_state()``.
"""
from __future__ import annotations

import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from .emb import EmbUnavailable


class WilcomState(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    READY = "READY"
    DESIGN_OPEN = "DESIGN_OPEN"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


_ALLOWED = {
    WilcomState.NOT_CONFIGURED: {WilcomState.CONNECTING, WilcomState.STOPPED},
    WilcomState.CONNECTING: {WilcomState.CONNECTED, WilcomState.ERROR, WilcomState.STOPPED},
    WilcomState.CONNECTED: {WilcomState.READY, WilcomState.ERROR, WilcomState.STOPPED},
    WilcomState.READY: {WilcomState.DESIGN_OPEN, WilcomState.EXECUTING, WilcomState.ERROR, WilcomState.STOPPED},
    WilcomState.DESIGN_OPEN: {WilcomState.EXECUTING, WilcomState.VERIFYING, WilcomState.READY, WilcomState.ERROR, WilcomState.STOPPED},
    WilcomState.EXECUTING: {WilcomState.DESIGN_OPEN, WilcomState.VERIFYING, WilcomState.ERROR, WilcomState.STOPPED},
    WilcomState.VERIFYING: {WilcomState.DESIGN_OPEN, WilcomState.READY, WilcomState.ERROR, WilcomState.STOPPED},
    WilcomState.ERROR: {WilcomState.NOT_CONFIGURED, WilcomState.CONNECTING, WilcomState.STOPPED},
    WilcomState.STOPPED: {WilcomState.NOT_CONFIGURED, WilcomState.CONNECTING},
}


class IllegalTransition(Exception):
    pass


class EMBBackendV2(ABC):
    name = "abstract"
    requires = ""          # human-readable configuration requirement

    @abstractmethod
    def detect_capability(self) -> dict: ...
    @abstractmethod
    def create(self, design: dict, path: str) -> str: ...
    @abstractmethod
    def open(self, path: str) -> dict: ...                   # {"objects":[{...}], "width_mm", "height_mm", ...}
    @abstractmethod
    def add_object(self, obj: dict) -> None: ...
    @abstractmethod
    def modify_object(self, index: int, changes: dict) -> None: ...
    @abstractmethod
    def delete_object(self, index: int) -> None: ...
    @abstractmethod
    def resequence(self, order: list[int]) -> None: ...
    @abstractmethod
    def save(self, path: str) -> str: ...


class UnavailableBackend(EMBBackendV2):
    name = "unavailable"
    requires = "a supported Wilcom web/API, SDK or locally installed Wilcom environment"

    def _no(self, what):
        raise EmbUnavailable("EMB_API_NOT_CONFIGURED", f"{what}: Wilcom native export backend is not configured")

    def detect_capability(self):
        return {"backend": self.name, "state": "REQUIRES_WILCOM_CONFIGURATION", "can_create": False, "can_edit": False,
                "reason": "Wilcom native export backend is not configured.", "requires": self.requires}
    def create(self, design, path): self._no("create")
    def open(self, path): self._no("open")
    def add_object(self, obj): self._no("add_object")
    def modify_object(self, index, changes): self._no("modify_object")
    def delete_object(self, index): self._no("delete_object")
    def resequence(self, order): self._no("resequence")
    def save(self, path): self._no("save")


class _ConfiguredButUnimplemented(UnavailableBackend):
    """Environment says a route exists, but this build has no verified protocol for it: refuse, never fake."""
    env_vars: tuple = ()

    def _configured(self) -> bool:
        return all(os.environ.get(v) for v in self.env_vars)

    def detect_capability(self):
        if not self._configured():
            return {"backend": self.name, "state": "REQUIRES_WILCOM_CONFIGURATION", "can_create": False, "can_edit": False,
                    "reason": f"set {', '.join(self.env_vars)}", "requires": self.requires}
        return {"backend": self.name, "state": "CONFIGURED_PROTOCOL_NOT_IMPLEMENTED", "can_create": False, "can_edit": False,
                "reason": "environment is configured, but this build has no verified client for this route (EXPERIMENTAL stub). "
                          "Implement EMBBackendV2 against your licensed Wilcom interface.", "requires": self.requires}

    def _no(self, what):
        raise EmbUnavailable("BACKEND_PROTOCOL_NOT_IMPLEMENTED", f"{self.name}.{what}: no verified client for this Wilcom route in this build")


class WilcomWebAPIBackend(_ConfiguredButUnimplemented):
    name = "wilcom_web_api"
    env_vars = ("EMBCOPILOT_WILCOM_API_URL", "EMBCOPILOT_WILCOM_API_KEY")
    requires = "a supported/licensed Wilcom web API endpoint"


class WilcomEWABackend(_ConfiguredButUnimplemented):
    name = "wilcom_supported_api"
    env_vars = ("EMBCOPILOT_WILCOM_SDK",)
    requires = "a supported Wilcom SDK / API capability you are licensed for"


class WilcomDesktopBridge(_ConfiguredButUnimplemented):
    name = "wilcom_desktop_bridge"
    env_vars = ("EMBCOPILOT_WILCOM_EXE",)
    requires = "a locally installed, licensed Wilcom (Windows) with a supported automation interface"

    def detect_capability(self):
        c = super().detect_capability()
        if not sys.platform.startswith("win"):
            c.update(state="REQUIRES_WILCOM_CONFIGURATION", reason="desktop bridge needs Windows with Wilcom installed")
        return c


@dataclass
class ValidationStage:
    stage: str
    ok: bool
    detail: str = ""


@dataclass
class EMBValidation:
    ok: bool
    stages: list[ValidationStage] = field(default_factory=list)
    code: str = "SUCCESS"

    def to_dict(self):
        return {"ok": self.ok, "code": self.code, "stages": [s.__dict__ for s in self.stages]}


class NativeEMBAdapter:
    BACKENDS = (WilcomWebAPIBackend, WilcomEWABackend, WilcomDesktopBridge)

    def __init__(self, backend: EMBBackendV2 | None = None):
        self.backend = backend or self._autodetect()
        self.state = WilcomState.NOT_CONFIGURED
        self.log: list[dict] = []
        self._validated_ok = False
        self.stopped = False

    def _autodetect(self) -> EMBBackendV2:
        for B in self.BACKENDS:
            b = B()
            if b.detect_capability()["state"] != "REQUIRES_WILCOM_CONFIGURATION":
                return b
        return UnavailableBackend()

    # ---- state machine -------------------------------------------------
    def _go(self, new: WilcomState):
        if new not in _ALLOWED[self.state] and new != self.state:
            raise IllegalTransition(f"{self.state.value} -> {new.value}")
        self.log.append({"t": time.strftime("%H:%M:%S"), "from": self.state.value, "to": new.value})
        self.state = new

    def emergency_stop(self):
        self.stopped = True
        self.state = WilcomState.STOPPED
        self.log.append({"t": time.strftime("%H:%M:%S"), "event": "EMERGENCY_STOP"})

    # ---- interface -----------------------------------------------------
    def detect_capability(self) -> dict:
        c = self.backend.detect_capability()
        c["adapter_state"] = self.state.value
        c["verified_export"] = self._validated_ok
        c["button"] = self.button_state()
        return c

    def button_state(self) -> dict:
        cap = self.backend.detect_capability()
        if cap["state"] == "REQUIRES_WILCOM_CONFIGURATION":
            return {"label": "Configure EMB Export", "enabled": False, "reason": cap.get("reason", "")}
        if not self._validated_ok:
            return {"label": "EMB Unavailable", "enabled": False,
                    "reason": "backend not verified: run validate() (generate-save-reopen-verify) successfully first"}
        return {"label": "Export EMB", "enabled": True, "reason": ""}

    def ui_message(self) -> dict:
        cap = self.backend.detect_capability()
        if cap["state"] == "REQUIRES_WILCOM_CONFIGURATION" or not self._validated_ok:
            return {"title": "Native EMB Export Unavailable", "reason": cap.get("reason") or "native backend not verified",
                    "available_exports": ["DST", "EXP"], "configure": "Wilcom Web/API/Desktop bridge (see EMB.md / WILCOM.md)"}
        return {"title": "Native EMB export ready", "reason": "", "available_exports": ["DST", "EXP", "EMB"], "configure": ""}

    def inspect(self, path: str) -> dict:
        from .emb import EMBAdapter
        return EMBAdapter().inspect(path)    # byte-level metadata only, never an object model

    def create(self, design, path):
        if self.stopped: raise EmbUnavailable("STOPPED", "emergency stop active")
        return self.backend.create(design, path)

    def open(self, path):
        return self.backend.open(path)

    def add_object(self, obj): return self.backend.add_object(obj)
    def modify_object(self, i, ch): return self.backend.modify_object(i, ch)
    def delete_object(self, i): return self.backend.delete_object(i)
    def resequence(self, order): return self.backend.resequence(order)
    def save(self, path): return self.backend.save(path)

    def export(self, design: dict, path: str) -> dict:
        """The ONLY way design.emb is produced: create -> save -> reopen -> verify.  Otherwise EMB_EXPORT_FAILED."""
        v = self.validate(design, path)
        if not v.ok:
            if os.path.exists(path):
                os.remove(path)    # never leave a non-validated .emb behind
            return {"status": "EMB_EXPORT_FAILED", "code": v.code, "validation": v.to_dict(), "download": False}
        self._validated_ok = True
        return {"status": "EMB_EXPORT_VERIFIED", "path": path, "validation": v.to_dict(), "download": True}

    def validate(self, design: dict, path: str) -> EMBValidation:
        stages: list[ValidationStage] = []
        def fail(code, st, detail):
            stages.append(ValidationStage(st, False, detail)); return EMBValidation(False, stages, code)
        cap = self.backend.detect_capability()
        if cap["state"] == "REQUIRES_WILCOM_CONFIGURATION":
            return fail("REQUIRES_WILCOM_CONFIGURATION", "capability", cap.get("reason", ""))
        try:
            self._go(WilcomState.CONNECTING) if self.state in (WilcomState.NOT_CONFIGURED, WilcomState.STOPPED, WilcomState.ERROR) else None
            self.create(design, path); stages.append(ValidationStage("generate+save", True))
        except EmbUnavailable as e:
            self.state = WilcomState.ERROR
            return fail(e.code, "generate+save", str(e))
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return fail("EMB_EXPORT_FAILED", "file_exists", "file missing or empty")
        stages.append(ValidationStage("file_exists", True))
        try:
            info = self.open(path)                       # reopen through the NATIVE mechanism, not our own parser
        except EmbUnavailable as e:
            return fail(e.code, "reopen", str(e))
        stages.append(ValidationStage("reopen", True))
        n_expected = len(design.get("objects", []))
        n_got = len(info.get("objects", []))
        if n_got != n_expected:
            return fail("EMB_EXPORT_FAILED", "object_count", f"{n_got} != {n_expected}")
        stages.append(ValidationStage("object_count", True, str(n_got)))
        w, h = design.get("width_mm"), design.get("height_mm")
        if w is not None and (abs(info.get("width_mm", -1e9) - w) > 0.5 or abs(info.get("height_mm", -1e9) - h) > 0.5):
            return fail("EMB_EXPORT_FAILED", "dimensions", "dimensions differ by > 0.5 mm")
        stages.append(ValidationStage("dimensions", True))
        return EMBValidation(True, stages)

    def edit_test(self, path: str) -> dict:
        """Object-level edit round trip (section 68): change density + thread, save, reopen, verify."""
        before = self.open(path)
        self.modify_object(0, {"density_mm": 0.37, "thread": "TEST"})
        self.save(path)
        after = self.open(path)
        o0 = after["objects"][0]
        ok = abs(o0.get("density_mm", 0) - 0.37) < 1e-6 and o0.get("thread") == "TEST" and len(after["objects"]) == len(before["objects"])
        return {"ok": ok, "before_objects": len(before["objects"]), "after_objects": len(after["objects"])}
