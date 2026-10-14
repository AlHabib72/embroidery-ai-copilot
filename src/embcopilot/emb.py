"""Native EMB workflow interface (spec 35-38).

There is no legitimate native EMB authoring path available in this project:
Wilcom's EMB is proprietary; we do not reverse-engineer it, and no supported
Wilcom API/service is configured. EMBAdapter therefore exposes the full
interface but every authoring call returns an explicit status instead of a file.

To enable: implement EMBBackend for a supported route (e.g. an official Wilcom
SDK/service you are licensed for, or UI automation of an installed Wilcom) and
set EMBCOPILOT_EMB_BACKEND. Until a backend passes `verify_round_trip`, the
capability may not be reported as IMPLEMENTED."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


class EmbUnavailable(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class EMBBackend(ABC):
    name = "abstract"

    @abstractmethod
    def create(self, design, path: str) -> str: ...
    @abstractmethod
    def open(self, path: str) -> dict: ...
    @abstractmethod
    def edit(self, path: str, operation: dict) -> None: ...


@dataclass
class EmbStatus:
    state: str  # UNAVAILABLE | EMB_API_NOT_CONFIGURED | PARTIAL | IMPLEMENTED
    detail: str

    def to_dict(self):
        return self.__dict__.copy()


class EMBAdapter:
    """detect / inspect / open / create / edit / save / export"""

    def __init__(self, backend: EMBBackend | None = None):
        self.backend = backend
        self._verified = False

    def status(self) -> EmbStatus:
        if self.backend is None:
            return EmbStatus("EMB_API_NOT_CONFIGURED", "no supported Wilcom API/workflow configured; native EMB creation and editing are UNAVAILABLE")
        return EmbStatus("IMPLEMENTED" if self._verified else "PARTIAL",
                         "backend configured; IMPLEMENTED only after verify_round_trip succeeds" if not self._verified else "round-trip verified")

    def detect(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        from .automation.emb_adapter import adapter_for
        ad = adapter_for(path)
        return bool(ad and ad.detect(path))

    def inspect(self, path: str) -> dict:
        from .automation.emb_adapter import adapter_for
        ad = adapter_for(path)
        if ad is None:
            raise EmbUnavailable("EMB_UNRECOGNISED", "not an EMB file")
        return {"metadata": ad.metadata(path), "note": "byte-level metadata only; object model not readable without a supported workflow"}

    def _need(self):
        if self.backend is None:
            raise EmbUnavailable("EMB_API_NOT_CONFIGURED", "no supported native workflow configured")
        return self.backend

    def open(self, path):
        return self._need().open(path)

    def create(self, design, path):
        out = self._need().create(design, path)
        info = self._need().open(out)  # never report success unless it re-opens
        if not info:
            raise EmbUnavailable("EMB_VERIFICATION_FAILED", "created file could not be re-opened")
        return out

    def edit(self, path, operation):
        return self._need().edit(path, operation)

    def save(self, path):
        raise EmbUnavailable("EMB_API_NOT_CONFIGURED", "save requires a configured native backend")

    def export(self, path, fmt):
        raise EmbUnavailable("EMB_API_NOT_CONFIGURED", "export requires a configured native backend")

    def verify_round_trip(self, design, tmp_path) -> bool:
        b = self._need()
        b.create(design, tmp_path)
        before = b.open(tmp_path)
        b.edit(tmp_path, {"op": "change_density", "object": 0, "value": 0.36})
        after = b.open(tmp_path)
        self._verified = bool(before and after and after != before)
        return self._verified
