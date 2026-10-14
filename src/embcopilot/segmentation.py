"""
Segmentation providers + model manager (spec 9-12, 54-55).

  SegmentationProvider
     ├── ClassicalSegmentation   IMPLEMENTED  (this project's Vision V2; always available, offline)
     └── SAM2Segmentation        EXPERIMENTAL (needs `torch` + `sam2` + a checkpoint; code path written against the
                                 documented SAM 2 API but NOT exercised here - no sam2/torch/GPU in the build sandbox)

Whatever produces masks, they go through the same `objects_from_masks` clean-up -> topology repair -> contours ->
EmbroideryObject path, so noisy masks never reach stitch generation raw. If a model is unavailable the application
falls back to Standard and says so (`vision_mode`), never crashing.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class SegmentationUnavailable(Exception):
    pass


class Cancelled(Exception):
    pass


@dataclass
class ModelStatus:
    name: str
    state: str  # INSTALLED | AVAILABLE | UNAVAILABLE
    detail: str
    device: str = "cpu"
    memory_needed_gb: float | None = None
    version: str = ""
    license: str = ""

    def to_dict(self):
        return self.__dict__.copy()


def _mem_available_gb() -> float | None:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        return None
    return None


class ModelManager:
    """Lazy loading, caching, unloading, GPU detection, memory check. Nothing heavy is imported at start-up."""

    def __init__(self):
        self._loaded: dict[str, object] = {}
        self._lock = threading.Lock()

    def status(self) -> list[ModelStatus]:
        out = [ModelStatus("Standard (Vision V2)", "INSTALLED", "built in, offline, CPU", "cpu", 0.2, "0.5", "project")]
        has_torch = importlib.util.find_spec("torch") is not None
        has_sam2 = importlib.util.find_spec("sam2") is not None
        ckpt = os.environ.get("EMBCOPILOT_SAM2_CHECKPOINT", "")
        if has_torch and has_sam2 and ckpt and os.path.exists(ckpt):
            dev = "cuda" if self._cuda() else "cpu"
            out.append(ModelStatus("SAM 2", "INSTALLED", "torch + sam2 + checkpoint found (EXPERIMENTAL: unverified in this build)", dev, 4.0, "", "Apache-2.0 (verify for your checkpoint)"))
        else:
            missing = [n for n, ok in (("torch", has_torch), ("sam2", has_sam2), ("EMBCOPILOT_SAM2_CHECKPOINT file", bool(ckpt and os.path.exists(ckpt)))) if not ok]
            out.append(ModelStatus("SAM 2", "UNAVAILABLE", "missing: " + ", ".join(missing), "n/a", 4.0, "", "Apache-2.0 (verify for your checkpoint)"))
        out.append(ModelStatus("Advanced semantic model", "UNAVAILABLE", "not implemented in this version", "n/a"))
        return out

    @staticmethod
    def _cuda() -> bool:
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001
            return False

    def get(self, name: str, factory, need_gb: float = 4.0):
        with self._lock:
            if name not in self._loaded:
                need = need_gb
                avail = _mem_available_gb()
                if avail is not None and avail < need:
                    raise SegmentationUnavailable(f"only {avail:.1f} GB RAM available; {name} needs about {need} GB")
                self._loaded[name] = factory()
            return self._loaded[name]

    def unload(self, name: str | None = None):
        with self._lock:
            if name is None:
                self._loaded.clear()
            else:
                self._loaded.pop(name, None)


MODELS = ModelManager()


class SegmentationProvider(ABC):
    name = "abstract"

    @abstractmethod
    def masks(self, image_path: str, cancel: threading.Event | None = None, timeout_s: float = 120.0) -> list: ...


class ClassicalSegmentation(SegmentationProvider):
    name = "Standard"

    def analyze(self, image_path, target_mm):
        from .vision import LocalVisionProvider
        return LocalVisionProvider().analyze(image_path, target_mm)

    def masks(self, image_path, cancel=None, timeout_s=120.0):
        raise NotImplementedError("ClassicalSegmentation produces objects directly via analyze()")


class SAM2Segmentation(SegmentationProvider):
    """EXPERIMENTAL. Modes: automatic, point prompt, box prompt. Config from env:
    EMBCOPILOT_SAM2_CHECKPOINT, EMBCOPILOT_SAM2_CONFIG (e.g. configs/sam2.1/sam2.1_hiera_l.yaml)."""
    name = "SAM2"

    def _predictor(self):
        if importlib.util.find_spec("torch") is None or importlib.util.find_spec("sam2") is None:
            raise SegmentationUnavailable("torch/sam2 not installed")
        ckpt, cfg = os.environ.get("EMBCOPILOT_SAM2_CHECKPOINT"), os.environ.get("EMBCOPILOT_SAM2_CONFIG")
        if not ckpt or not os.path.exists(ckpt) or not cfg:
            raise SegmentationUnavailable("EMBCOPILOT_SAM2_CHECKPOINT / EMBCOPILOT_SAM2_CONFIG not configured")

        def build():
            from sam2.build_sam import build_sam2
            dev = "cuda" if ModelManager._cuda() else "cpu"
            return build_sam2(cfg, ckpt, device=dev)
        return MODELS.get("sam2", build)

    def masks(self, image_path, cancel=None, timeout_s=120.0, points=None, box=None):
        import cv2
        import numpy as np
        model = self._predictor()
        img = cv2.cvtColor(cv2.imread(image_path), cv2.COLOR_BGR2RGB)
        t0 = time.time()
        if points or box:
            from sam2.sam2_image_predictor import SAM2ImagePredictor
            pred = SAM2ImagePredictor(model); pred.set_image(img)
            m, _s, _l = pred.predict(point_coords=np.array(points["xy"]) if points else None,
                                     point_labels=np.array(points["labels"]) if points else None,
                                     box=np.array(box) if box else None, multimask_output=False)
            return [m[0].astype(bool)]
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        gen = SAM2AutomaticMaskGenerator(model)
        out = []
        for m in gen.generate(img):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if time.time() - t0 > timeout_s:
                raise SegmentationUnavailable("SAM2 inference timeout")
            out.append(m["segmentation"].astype(bool))
        return out


def clean_mask(mask, min_area_px=40, min_hole_px=12):
    """Mask cleanup + topology repair: binarise, open/close, drop specks, fill tiny holes (real holes are kept)."""
    import cv2
    import numpy as np
    m = (mask > 0).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, cc, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < min_area_px:
            m[cc == i] = 0
    inv = (m == 0).astype(np.uint8)
    n, cc, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    border = set(np.unique(np.concatenate([cc[0], cc[-1], cc[:, 0], cc[:, -1]])))
    for i in range(1, n):
        if i not in border and st[i, cv2.CC_STAT_AREA] < min_hole_px:
            m[cc == i] = 255
    return m


def objects_from_masks(masks: list, image_bgr, target_mm, min_area_px=40):
    """Masks (any provider) -> cleaned -> per-component EmbroideryObjects (holes preserved) + ObjectBlueprint.
    Overlapping masks are resolved by giving each pixel to the SMALLER mask (foreground detail wins)."""
    import cv2
    import numpy as np
    from .vision import LocalVisionProvider, ObjectBlueprint
    h, w = image_bgr.shape[:2]
    order = sorted(range(len(masks)), key=lambda i: int(masks[i].sum()), reverse=True)  # big first, small overwrite
    label = np.full((h, w), -1, np.int32)
    for k, i in enumerate(order):
        label[masks[i] > 0] = k
    bp = ObjectBlueprint(source_image="<masks>", image_size_px=(w, h), target_size_mm=target_mm,
                         background={"mode": "model_masks", "confidence": 0.5})
    lv = LocalVisionProvider(min_area_px=min_area_px)
    sx, sy = target_mm[0] / w, target_mm[1] / h
    px_mm = (sx * sy) ** 0.5
    seg = np.zeros_like(image_bgr); fg = np.zeros((h, w), np.uint8); idx = 0
    for k in range(len(order)):
        comp_mask = clean_mask(label == k, min_area_px)
        n, cc = cv2.connectedComponents(comp_mask, connectivity=8)
        for j in range(1, n):
            comp = (cc == j).astype(np.uint8) * 255
            col = tuple(int(v) for v in cv2.mean(image_bgr, mask=cv2.erode(comp, np.ones((3, 3), np.uint8)) if comp.sum() > 2000 else comp)[:3])
            obj = lv._build_object(cv2, np, comp, col, bp, sx, sy, px_mm, idx + 1)
            if obj is None:
                continue
            idx += 1; obj.source = "vision:mask_provider"; bp.objects.append(obj)
            seg[comp > 0] = col; fg |= comp
    bp.debug_images = {"original": image_bgr, "foreground_mask": fg, "segmentation": seg}
    return bp


def get_provider(name: str):
    """Returns (provider, vision_mode_label, warnings). Falls back to Standard, never raises for a missing model."""
    if name.lower() in ("sam2", "sam"):
        try:
            p = SAM2Segmentation(); p._predictor()
            return p, "SAM2 (EXPERIMENTAL)", []
        except SegmentationUnavailable as e:
            return ClassicalSegmentation(), "Standard (SAM2 unavailable)", [f"SAM2_UNAVAILABLE: {e}; fell back to the standard Vision Engine"]
    return ClassicalSegmentation(), "Standard", []
