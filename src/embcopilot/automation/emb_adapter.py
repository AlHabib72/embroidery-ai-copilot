"""
DesignFileAdapter abstraction (project brief section 19).

DST is fully supported (it wraps this project's own Phase 1 parser — real,
tested, working). EMB is Wilcom's proprietary, undocumented native format.
This project does NOT reverse-engineer it, bypass its licensing, or pretend
to parse structures it cannot actually read. EMBAdapter therefore only
implements what is legitimate and reliable without doing any of that:
file detection by extension and a best-effort, undecoded byte-signature
read — nothing that claims to understand EMB's internal object model.

Every adapter reports its capabilities as one of:
    SUPPORTED | PARTIAL | EXPERIMENTAL | PLANNED | UNAVAILABLE
never silently returning empty/default data in place of an honest
"unavailable".
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..domain import CapabilityState
from ..dst import read_dst
from ..models import Design
from ..vision import NotImplementedCapability


@dataclass
class FileCapabilities:
    detect: str = CapabilityState.UNAVAILABLE.value
    metadata: str = CapabilityState.UNAVAILABLE.value
    read_stitches: str = CapabilityState.UNAVAILABLE.value
    write_stitches: str = CapabilityState.UNAVAILABLE.value
    handoff: str = CapabilityState.UNAVAILABLE.value  # e.g. "open the native app on this file"

    def to_dict(self) -> dict:
        return {
            "detect": self.detect,
            "metadata": self.metadata,
            "read_stitches": self.read_stitches,
            "write_stitches": self.write_stitches,
            "handoff": self.handoff,
        }


class DesignFileAdapter(ABC):
    extension: str = ""

    @abstractmethod
    def capabilities(self) -> FileCapabilities: ...

    @abstractmethod
    def detect(self, path: str) -> bool: ...

    @abstractmethod
    def metadata(self, path: str) -> dict: ...


class DSTAdapter(DesignFileAdapter):
    extension = ".dst"

    def capabilities(self) -> FileCapabilities:
        return FileCapabilities(
            detect=CapabilityState.SUPPORTED.value,
            metadata=CapabilityState.SUPPORTED.value,
            read_stitches=CapabilityState.SUPPORTED.value,
            write_stitches=CapabilityState.SUPPORTED.value,
            handoff=CapabilityState.UNAVAILABLE.value,  # no "open in Wilcom" bridge exists yet
        )

    def detect(self, path: str) -> bool:
        return path.lower().endswith(self.extension)

    def metadata(self, path: str) -> dict:
        design = read_dst(path)
        w, h = design.size_mm()
        return {"header": design.header, "width_mm": w, "height_mm": h}

    def read(self, path: str) -> Design:
        return read_dst(path)


class EMBAdapter(DesignFileAdapter):
    """Detect-only. Never claims to parse EMB's internal object model.

    `metadata()` returns only what can be read WITHOUT interpreting any
    proprietary structure: file size and the first bytes as a raw hex
    signature, purely for the user's own reference (e.g. to eyeball
    whether two files are byte-identical) — not decoded, not claimed to
    mean anything about the design's content.
    """

    extension = ".emb"

    def capabilities(self) -> FileCapabilities:
        return FileCapabilities(
            detect=CapabilityState.SUPPORTED.value,
            metadata=CapabilityState.PARTIAL.value,  # file-level only, not design content
            read_stitches=CapabilityState.UNAVAILABLE.value,
            write_stitches=CapabilityState.UNAVAILABLE.value,
            handoff=CapabilityState.PLANNED.value,  # "open in Wilcom" bridge is future work
        )

    def detect(self, path: str) -> bool:
        return path.lower().endswith(self.extension)

    def metadata(self, path: str) -> dict:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            header_bytes = fh.read(16)
        return {
            "file_size_bytes": size,
            "first_16_bytes_hex": header_bytes.hex(),
            "note": (
                "EMB native editing capability: Not available in current build. "
                "This is Wilcom's proprietary, undocumented format; this project "
                "does not reverse-engineer or bypass it. The bytes above are shown "
                "undecoded, for reference only."
            ),
        }

    def handoff_hint(self, path: str) -> str:
        """What a human should do today, until a real handoff bridge exists."""
        return (
            f"Open '{path}' directly in Wilcom ES-65 to view or edit it. This "
            "project has no automation bridge to that step yet (see README "
            "'Wilcom support')."
        )


class _UnimplementedFormatAdapter(DesignFileAdapter):
    """Base for formats this project has not implemented at all yet."""

    format_name = "unknown"

    def capabilities(self) -> FileCapabilities:
        return FileCapabilities()  # everything UNAVAILABLE

    def detect(self, path: str) -> bool:
        return path.lower().endswith(self.extension)

    def metadata(self, path: str) -> dict:
        raise NotImplementedCapability(
            f"{self.format_name} support is planned but not implemented in this build."
        )


class PESAdapter(_UnimplementedFormatAdapter):
    extension = ".pes"
    format_name = "PES"


class JEFAdapter(_UnimplementedFormatAdapter):
    extension = ".jef"
    format_name = "JEF"


ADAPTERS: list[DesignFileAdapter] = [DSTAdapter(), EMBAdapter(), PESAdapter(), JEFAdapter()]


def adapter_for(path: str) -> DesignFileAdapter | None:
    for adapter in ADAPTERS:
        if adapter.detect(path):
            return adapter
    return None
