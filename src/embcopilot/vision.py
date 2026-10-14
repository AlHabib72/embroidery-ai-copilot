"""
Vision engine (v2, partial): turns a reference image into an Object Blueprint.

HONESTY NOTE (read before trusting output from this module): this is a
GEOMETRIC vision engine, not a semantic one. `LocalVisionProvider` finds
regions of similar colour and extracts their outlines and bounding boxes —
real, working image processing (colour quantisation + contour extraction,
via OpenCV). It does NOT know that a shape is a "wing" or a "leaf"; every
detected object's category defaults to ObjectCategory.UNKNOWN unless the
caller supplies a category afterwards (e.g. from a human, or from a future
semantic model plugged in as another VisionEngine implementation). Object
naming in this module is always positional ("Region 1", "Region 2"), never
an invented semantic guess — see PROJECT_CONTEXT section 10: "Do not
generate fake perfect vector geometry when confidence is low."

Every VisionEngine implementation is expected to be swappable — see
CloudVisionProvider below, which is an unimplemented placeholder raising
NotImplementedCapability, not a stub that silently does nothing.
"""

from __future__ import annotations

import math

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .domain import Confidence, EmbroideryObject, ObjectCategory, new_id


class NotImplementedCapability(Exception):
    """Raised by a capability that is architecturally present but not yet
    implemented, instead of silently doing nothing or faking a result."""


@dataclass
class ObjectBlueprint:
    """Output of a VisionEngine: a set of detected regions, no stitch info.

    `image_size_px` and `target_size_mm` are both recorded so every
    downstream consumer can convert between pixels and millimetres
    correctly instead of assuming 1 px = 1 mm.
    """

    source_image: str
    image_size_px: tuple[int, int]
    target_size_mm: tuple[float, float]
    objects: list[EmbroideryObject] = field(default_factory=list)
    # Vision V2: what the engine decided about the background, and why.
    background: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Debug arrays (numpy), never serialised: original, normalized, masks...
    debug_images: dict = field(default_factory=dict, repr=False, compare=False)

    def px_to_mm(self, x_px: float, y_px: float) -> tuple[float, float]:
        w_px, h_px = self.image_size_px
        w_mm, h_mm = self.target_size_mm
        return (x_px / max(1, w_px) * w_mm, y_px / max(1, h_px) * h_mm)

    def to_dict(self) -> dict:
        return {
            "source_image": self.source_image,
            "image_size_px": self.image_size_px,
            "target_size_mm": self.target_size_mm,
            "background": self.background,
            "warnings": self.warnings,
            "objects": [o.to_dict() for o in self.objects],
        }


class VisionEngine(ABC):
    """Pluggable vision interface. Do not lock the system to one provider."""

    @abstractmethod
    def analyze(self, image_path: str, target_size_mm: tuple[float, float]) -> ObjectBlueprint:
        raise NotImplementedError


class CloudVisionProvider(VisionEngine):
    """Placeholder for a future cloud/semantic vision provider.

    Deliberately unimplemented: this project does not silently send
    reference images to an external service. Enabling this would require
    explicit user opt-in and disclosure (see README "Privacy"). Until then
    it raises NotImplementedCapability rather than faking a result.
    """

    def analyze(self, image_path: str, target_size_mm: tuple[float, float]) -> ObjectBlueprint:
        raise NotImplementedCapability(
            "CloudVisionProvider is architecture-only in this build. No "
            "reference image is ever sent to an external service without "
            "explicit opt-in — see README 'Privacy'."
        )


class LocalVisionProvider(VisionEngine):
    """Real, offline, geometry-only object detection (Vision Engine v2, partial).

    Pipeline (each stage is implemented, none is faked):
      1. load with alpha preserved (original never modified)
      2. background model: transparent alpha, OR a per-channel plane fitted to
         the image border (handles solid, near-white and simple gradients),
         with a border-agreement confidence; if the border is not
         background-like the engine says so instead of guessing
      3. foreground / background / uncertain masks (uncertain = anti-aliased
         boundary band)
      4. normalisation: median filter (JPEG/AA noise), morphological cleanup,
         small-island removal, small-hole filtering
      5. palette from eroded foreground only (so anti-aliased fringe colours
         never become palette entries), then per-pixel un-mixing against the
         local background so fringe pixels join their true object
      6. per-colour connected components; contours WITH hierarchy (holes and
         nested islands preserved, never filled)
      7. distance-transform width profile + Zhang-Suen skeleton; centerline
         only where the shape is genuinely elongated

    NOT implemented here (see LIMITATIONS.md): semantic classification
    (category stays UNKNOWN), overlap/occlusion reasoning between objects of
    different colour, object grouping across colours, curvature analysis.
    Confidence is a geometric proxy, not a trained model's probability.
    """

    def __init__(
        self,
        max_colors: int = 8,
        min_area_px: int = 40,
        simplify_epsilon_frac: float = 0.005,
        bg_tolerance: float = 40.0,
        min_hole_area_px: int = 12,
        palette_merge_dist: float = 40.0,
        min_border_agreement: float = 0.85,
        sliver_absorb_max_color_dist: float = 60.0,
        centerline_min_elongation: float = 3.0,
        max_analysis_px: int = 768,
    ):
        self.max_colors = max_colors
        self.min_area_px = min_area_px
        self.simplify_epsilon_frac = simplify_epsilon_frac
        self.bg_tolerance = bg_tolerance
        self.min_hole_area_px = min_hole_area_px
        self.palette_merge_dist = palette_merge_dist
        self.min_border_agreement = min_border_agreement
        self.sliver_absorb_max_color_dist = sliver_absorb_max_color_dist
        self.centerline_min_elongation = centerline_min_elongation
        self.max_analysis_px = max_analysis_px

    # ------------------------------------------------------------------ API
    def analyze(self, image_path: str, target_size_mm: tuple[float, float]) -> ObjectBlueprint:
        try:
            import cv2
            import numpy as np
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise NotImplementedCapability(
                "LocalVisionProvider requires opencv-python and numpy, which "
                "are not installed in this environment."
            ) from exc

        raw = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise ValueError(f"Could not read image: {image_path}")
        if raw.dtype != np.uint8:
            raw = cv2.convertScaleAbs(raw, alpha=255.0 / max(1, int(raw.max())))
        alpha = None
        if raw.ndim == 2:
            bgr = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
        elif raw.shape[2] == 4:
            bgr, alpha = raw[:, :, :3].copy(), raw[:, :, 3].copy()
        else:
            bgr = raw[:, :, :3].copy()
        h_px, w_px = bgr.shape[:2]
        if h_px < 4 or w_px < 4:
            raise ValueError(f"Image too small to analyse: {w_px}x{h_px}")

        bp = ObjectBlueprint(source_image=image_path, image_size_px=(w_px, h_px), target_size_mm=target_size_mm)
        # All pixel thresholds (fringe width, min area, hole size) are tuned for
        # a bounded working resolution, which also keeps run time predictable
        # and makes results stable across export resolutions of one design.
        # Geometry is reported in mm, so downscaling does not change units.
        longest = max(h_px, w_px)
        if longest > self.max_analysis_px:
            f = self.max_analysis_px / longest
            new_size = (max(4, round(w_px * f)), max(4, round(h_px * f)))
            bgr = cv2.resize(bgr, new_size, interpolation=cv2.INTER_AREA)
            if alpha is not None:
                alpha = cv2.resize(alpha, new_size, interpolation=cv2.INTER_AREA)
            bp.warnings.append(
                f"analysed at {new_size[0]}x{new_size[1]}px (downscaled from {w_px}x{h_px}); "
                "features thinner than ~1 working pixel may be lost"
            )
            h_px, w_px = bgr.shape[:2]
        bp.debug_images["original"] = bgr.copy()

        # 4a. normalisation (edge-preserving-ish): median removes JPEG/AA speckle
        norm = cv2.medianBlur(bgr, 3)
        bp.debug_images["normalized"] = norm

        # 2/3. background + masks
        fg, uncertain, bg_color_map = self._background(cv2, np, norm, alpha, bp)
        fg, removed_islands = self._cleanup_mask(cv2, np, fg)
        if removed_islands:
            bp.warnings.append(f"removed {removed_islands} noise island(s) smaller than {self.min_area_px}px")
        bp.debug_images["foreground_mask"] = fg
        bp.debug_images["uncertain_mask"] = uncertain
        bp.debug_images["background_mask"] = ((fg == 0) & (uncertain == 0)).astype(np.uint8) * 255

        if not fg.any():
            bp.warnings.append("no foreground detected: image appears to be only background")
            return bp

        # 5. palette + un-mixed labelling
        labels, palette = self._label(cv2, np, norm, fg, bg_color_map)
        seg_vis = np.zeros_like(bgr)
        for i, c in enumerate(palette):
            seg_vis[labels == i] = c
        bp.debug_images["segmentation"] = seg_vis

        sx = target_size_mm[0] / w_px
        sy = target_size_mm[1] / h_px
        px_mm = (sx * sy) ** 0.5  # geometric-mean scale for widths (isotropic assumption)
        if abs(sx - sy) / max(sx, sy) > 0.02:
            bp.warnings.append(
                "target size aspect ratio differs from the image's: widths use the geometric-mean "
                "scale and are approximate"
            )

        region_index = 0
        for ci, color in enumerate(palette):
            layer_mask = (labels == ci).astype(np.uint8) * 255
            layer_mask = cv2.morphologyEx(layer_mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)) \
                if self._is_thick(cv2, layer_mask) else layer_mask
            n_cc, cc = cv2.connectedComponents(layer_mask, connectivity=8)
            for k in range(1, n_cc):
                comp = (cc == k).astype(np.uint8) * 255
                obj = self._build_object(cv2, np, comp, color, bp, sx, sy, px_mm, region_index + 1)
                if obj is None:
                    continue
                region_index += 1
                bp.objects.append(obj)
        bp.debug_images["labels"] = labels
        return bp

    # ------------------------------------------------------------ background
    def _background(self, cv2, np, norm, alpha, bp):
        h, w = norm.shape[:2]
        tol = self.bg_tolerance
        if alpha is not None and alpha.min() < 250:
            transparent = alpha <= 16
            fg = (alpha >= 200).astype(np.uint8) * 255
            unc = ((alpha > 16) & (alpha < 200)).astype(np.uint8) * 255
            frac = float(transparent.mean())
            bp.background = {"mode": "alpha", "transparent_fraction": round(frac, 4), "confidence": 0.98}
            # Un-premultiply guess: colour under transparent pixels is meaningless.
            bg_map = np.zeros((h, w, 3), np.float32)
            return fg, unc, bg_map
        # Fit a plane (per channel) to the 1-pixel border, robustly.
        ys, xs = np.mgrid[0:h, 0:w]
        border = np.zeros((h, w), bool)
        b = max(1, int(round(min(h, w) * 0.01)))
        border[:b, :] = border[-b:, :] = True
        border[:, :b] = border[:, -b:] = True
        bx = xs[border].astype(np.float32) / w
        by = ys[border].astype(np.float32) / h
        A = np.stack([bx, by, np.ones_like(bx)], 1)
        bcol = norm[border].astype(np.float32)
        inlier = np.ones(len(bcol), bool)
        coef = None
        for _ in range(4):
            coef, *_ = np.linalg.lstsq(A[inlier], bcol[inlier], rcond=None)
            resid = np.linalg.norm(A @ coef - bcol, axis=1)
            new_inlier = resid <= tol
            if new_inlier.sum() < 0.3 * len(bcol) or (new_inlier == inlier).all():
                break
            inlier = new_inlier
        resid = np.linalg.norm(A @ coef - bcol, axis=1)
        agreement = float((resid <= tol).mean())
        gx, gy = xs.astype(np.float32) / w, ys.astype(np.float32) / h
        bg_map = np.stack([gx, gy, np.ones_like(gx)], -1) @ coef  # HxWx3 modelled background
        bg_map = np.clip(bg_map, 0, 255).astype(np.float32)
        dist = np.linalg.norm(norm.astype(np.float32) - bg_map, axis=2)
        if agreement < self.min_border_agreement:
            bp.background = {"mode": "none_detected", "confidence": round(1 - agreement, 3),
                             "border_agreement": round(agreement, 3)}
            bp.warnings.append(
                "image border is not uniform enough to be a background (design may be full-bleed); "
                "treating whole image as foreground. Provide a transparent PNG or a clean background."
            )
            return np.full((h, w), 255, np.uint8), np.zeros((h, w), np.uint8), bg_map
        fg = (dist > tol).astype(np.uint8) * 255
        unc = ((dist > tol * 0.5) & (dist <= tol)).astype(np.uint8) * 255
        colour = [int(v) for v in bg_map[border].mean(axis=0)[::-1]]  # report as RGB
        spread = float(np.linalg.norm(bg_map[0, 0] - bg_map[-1, -1]))
        bp.background = {
            "mode": "gradient" if spread > tol * 0.5 else "solid",
            "rgb": colour,
            "border_agreement": round(agreement, 3),
            "confidence": round(min(0.99, 0.5 + 0.5 * agreement), 3),
        }
        return fg, unc, bg_map

    def _cleanup_mask(self, cv2, np, fg):
        k3 = np.ones((3, 3), np.uint8)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k3)  # bridge 1px cracks
        # small-hole filtering: fill background components fully enclosed and tiny
        inv = (fg == 0).astype(np.uint8)
        n, cc, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
        border_labels = set(np.unique(np.concatenate([cc[0], cc[-1], cc[:, 0], cc[:, -1]])))
        for i in range(1, n):
            if i not in border_labels and stats[i, cv2.CC_STAT_AREA] < self.min_hole_area_px:
                fg[cc == i] = 255
        # small-island removal
        n, cc, stats, _ = cv2.connectedComponentsWithStats((fg > 0).astype(np.uint8), connectivity=8)
        removed = 0
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] < self.min_area_px:
                fg[cc == i] = 0
                removed += 1
        return fg, removed

    # ---------------------------------------------------------- colour layer
    def _label(self, cv2, np, norm, fg, bg_map):
        h, w = fg.shape
        # Palette comes from interior pixels only, so anti-aliased fringe colours
        # never become palette entries. Erode 2px if enough interior exists,
        # else 1px, else (very thin design) use all foreground.
        core = cv2.erode(fg, np.ones((3, 3), np.uint8), iterations=2) > 0
        if core.sum() < 50:
            core = cv2.erode(fg, np.ones((3, 3), np.uint8)) > 0
        if core.sum() < 20:
            core = fg > 0
        pix = norm[core].astype(np.float32)
        if len(pix) > 60000:
            pix = pix[np.random.RandomState(0).choice(len(pix), 60000, replace=False)]
        uniq = len(np.unique(pix, axis=0))
        k = max(1, min(self.max_colors, uniq))
        if k == 1:
            centers = pix.mean(axis=0, keepdims=True)
        else:
            crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
            cv2.setRNGSeed(0)
            _c, _l, centers = cv2.kmeans(pix, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
        centers = self._merge_close(np, centers, self.palette_merge_dist)
        centers = self._drop_background_blends(np, centers, bg_map)

        fgp = norm.astype(np.float32)
        d_euclid = np.stack([np.linalg.norm(fgp - c, axis=2) for c in centers], -1)
        # un-mixing residual against the local background (anti-aliased fringe)
        d_mix = np.full_like(d_euclid, 1e9)
        if bg_map.any():
            for i, c in enumerate(centers):
                v = c[None, None, :] - bg_map
                p = fgp - bg_map
                vv = (v * v).sum(-1) + 1e-6
                a = np.clip((p * v).sum(-1) / vv, 0.0, 1.0)
                d_mix[..., i] = np.linalg.norm(p - a[..., None] * v, axis=-1) + 6.0 * (1 - a)
        dist = np.minimum(d_euclid, d_mix)
        labels = dist.argmin(-1).astype(np.int32)
        labels[fg == 0] = -1
        # drop palette entries with too few pixels, reassign implicitly next pass
        keep = [i for i in range(len(centers)) if (labels == i).sum() >= self.min_area_px]
        if not keep:
            keep = [int(np.bincount(labels[labels >= 0]).argmax())]
        if len(keep) != len(centers):
            centers = centers[keep]
            dist = dist[..., keep]
            labels = dist.argmin(-1).astype(np.int32)
            labels[fg == 0] = -1
        labels = self._absorb_slivers(cv2, np, labels, centers)
        palette = [tuple(int(v) for v in c) for c in centers]  # BGR
        return labels, palette

    def _absorb_slivers(self, cv2, np, labels, centers):
        """Thin (<=2px) components that hug a differently-labelled neighbour of
        similar colour are compression/anti-alias halos, not objects: give them
        to that neighbour. A thin line touching ONLY background is kept.
        Known limitation: a genuine hairline drawn directly on a similar colour
        (< sliver_absorb_max_color_dist apart) is absorbed too."""
        k = len(centers)
        for i in range(k):
            m = (labels == i).astype(np.uint8)
            n, cc = cv2.connectedComponents(m, connectivity=8)
            for j in range(1, n):
                comp = (cc == j).astype(np.uint8)
                if cv2.distanceTransform(comp, cv2.DIST_L2, 3).max() > 2.0:
                    continue
                ring = cv2.dilate(comp, np.ones((5, 5), np.uint8)) - comp
                neigh = labels[ring > 0]
                neigh = neigh[(neigh >= 0) & (neigh != i)]
                if len(neigh) == 0:
                    continue
                target = int(np.bincount(neigh).argmax())
                if float(np.linalg.norm(centers[target] - centers[i])) <= self.sliver_absorb_max_color_dist:
                    labels[comp > 0] = target
        return labels

    @staticmethod
    def _drop_background_blends(np, centers, bg_map, residual_tol: float = 22.0):
        """Remove palette entries that are just anti-aliased mixtures of another
        palette colour and the background (e.g. a 55% blend of a dark letter
        with white). Otherwise upscaled/blurred images grow a fake 'halo'
        object around every shape."""
        if len(centers) < 2 or not bg_map.any():
            return centers
        bg = np.median(bg_map.reshape(-1, 3), axis=0)
        keep = []
        for i, c in enumerate(centers):
            is_blend = False
            for j, e in enumerate(centers):
                if i == j:
                    continue
                v = e - bg
                vv = float(v @ v)
                if vv < 1e-6:
                    continue
                a = float((c - bg) @ v) / vv
                resid = float(np.linalg.norm((c - bg) - a * v))
                if 0.08 < a < 0.92 and resid < residual_tol:
                    is_blend = True
                    break
            if not is_blend:
                keep.append(i)
        return centers[keep] if keep else centers

    @staticmethod
    def _merge_close(np, centers, dist):
        merged: list = []
        for c in centers:
            for i, m in enumerate(merged):
                if np.linalg.norm(m[0] - c) < dist:
                    merged[i] = ((m[0] * m[1] + c) / (m[1] + 1), m[1] + 1)
                    break
            else:
                merged.append((c.astype(np.float32), 1))
        return np.array([m[0] for m in merged], np.float32)

    @staticmethod
    def _is_thick(cv2, mask) -> bool:
        return int(cv2.distanceTransform(mask, cv2.DIST_L2, 3).max()) >= 3

    # ------------------------------------------------------------- objects
    def _build_object(self, cv2, np, comp, color_bgr, bp, sx, sy, px_mm, idx):
        contours, hier = cv2.findContours(comp, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
        if hier is None:
            return None
        hier = hier[0]
        outer_i = [i for i, hh in enumerate(hier) if hh[3] == -1]
        if not outer_i:
            return None
        oi = max(outer_i, key=lambda i: cv2.contourArea(contours[i]))
        outer = contours[oi]
        outer_area = cv2.contourArea(outer)
        holes_px = [contours[i] for i, hh in enumerate(hier)
                    if hh[3] == oi and cv2.contourArea(contours[i]) >= self.min_hole_area_px]
        hole_area = sum(cv2.contourArea(hc) for hc in holes_px)
        area_px = outer_area - hole_area
        pixel_count = int((comp > 0).sum())
        if max(area_px, pixel_count if outer_area < 1 else 0) < self.min_area_px and pixel_count < self.min_area_px:
            return None

        perim = cv2.arcLength(outer, True)
        eps = self.simplify_epsilon_frac * perim
        to_mm = lambda cnt: [(float(p[0][0]) * sx, float(p[0][1]) * sy) for p in cnt]  # noqa: E731
        simp = lambda cnt: cv2.approxPolyDP(cnt, self.simplify_epsilon_frac * cv2.arcLength(cnt, True), True)  # noqa: E731
        x, y, w, h = cv2.boundingRect(outer)

        # width profile + centerline on the component with holes preserved
        dt = cv2.distanceTransform(comp, cv2.DIST_L2, 5)
        skel = self._thin(np, comp > 0)
        widths_px = 2.0 * dt[skel] if skel.any() else np.array([2.0 * dt.max()])
        widths_mm = widths_px * px_mm
        stats = {
            "min": float(widths_mm.min()), "p05": float(np.percentile(widths_mm, 5)),
            "median": float(np.median(widths_mm)), "p95": float(np.percentile(widths_mm, 95)),
            "max": float(widths_mm.max()),
        }
        med_half = float(np.median(dt[skel])) if skel.any() else 1.0
        path, topo = self._skeleton_path(np, skel, min_spur_px=max(5.0, 1.8 * med_half))
        center_len_mm = self._polyline_len(path) * px_mm
        stats["centerline_length_mm"] = center_len_mm
        elong = center_len_mm / max(stats["median"], 1e-6)
        centerline = None
        cl_widths = None
        # A closed loop (ring) has no meaningful open centerline: strategies use
        # its outer/hole contours as rails instead (see strategies.satin_ring).
        if elong >= self.centerline_min_elongation and len(path) >= 2 and not topo.get("closed_loop"):
            step = max(1, len(path) // 200)
            sel = list(path[::step])
            if sel[-1] != path[-1]:
                sel.append(path[-1])
            centerline = [(float(px) * sx, float(py) * sy) for px, py in sel]
            # local width (mm) = 2 * distance-to-edge at each centerline point
            cl_widths = [float(2.0 * dt[py, px] * px_mm) for px, py in sel]
            # A distance transform always dips near a skeleton end (the skeleton stops short of the
            # boundary). Hold the interior width over the last ~1.5 widths at each end so satin rails,
            # occlusion merging and resolution-independence are not driven by that raster artefact.
            # (Cost: genuine tapered tips of elongated shapes are not represented in the width profile.)
            arc = np.concatenate([[0.0], np.cumsum([math.hypot(sel[i + 1][0] - sel[i][0], sel[i + 1][1] - sel[i][1]) for i in range(len(sel) - 1)])])
            trim_px = 1.5 * float(np.median([2.0 * dt[py, px] for px, py in sel]))
            lo = int(np.searchsorted(arc, trim_px)); hi = int(np.searchsorted(arc, arc[-1] - trim_px))
            if 0 < lo < hi < len(cl_widths) - 1:
                cl_widths[:lo] = [cl_widths[lo]] * lo
                cl_widths[hi + 1:] = [cl_widths[hi]] * (len(cl_widths) - hi - 1)

        b, g, r = color_bgr
        conf = self._confidence(area_px, perim, len(simp(outer)))
        obj = EmbroideryObject(
            object_id=new_id("obj"),
            name=f"Region {idx}",
            category=ObjectCategory.UNKNOWN.value,
            geometry=to_mm(simp(outer)),
            bbox_mm=(x * sx, y * sy, (x + w) * sx, (y + h) * sy),
            color_group=f"rgb({r},{g},{b})",
            confidence={"detection": conf},
            source="vision:v2_bgmask_contour_hierarchy",
            holes_mm=[to_mm(simp(hc)) for hc in holes_px],
            centerline_mm=centerline,
            area_mm2=float(area_px * sx * sy),
            width_stats_mm=stats,
            skeleton_topology=topo,
            centerline_widths_mm=cl_widths,
        )
        return obj

    # --------------------------------------------------------------- skeleton
    @staticmethod
    def _thin(np, img):
        """Zhang-Suen thinning (vectorised). `img` is a bool array."""
        im = np.pad(img.astype(np.uint8), 1)
        changed = True
        while changed:
            changed = False
            for step in (0, 1):
                P = [im[:-2, 1:-1], im[:-2, 2:], im[1:-1, 2:], im[2:, 2:], im[2:, 1:-1], im[2:, :-2], im[1:-1, :-2], im[:-2, :-2]]
                p2, p3, p4, p5, p6, p7, p8, p9 = P
                c = im[1:-1, 1:-1]
                B = sum(P)
                seq = P + [P[0]]
                A = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8))
                if step == 0:
                    m = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
                else:
                    m = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
                rm = (c == 1) & (B >= 2) & (B <= 6) & (A == 1) & m
                if rm.any():
                    im[1:-1, 1:-1][rm] = 0
                    changed = True
        return im[1:-1, 1:-1].astype(bool)

    @staticmethod
    def _skeleton_path(np, skel, min_spur_px: float = 0.0):
        ys, xs = np.nonzero(skel)
        if len(xs) == 0:
            return [], {"endpoints": 0, "branch_points": 0}
        pts = set(zip(xs.tolist(), ys.tolist()))
        def neighbours(pset):
            return {(x, y): [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                             if (dx or dy) and (x + dx, y + dy) in pset] for (x, y) in pset}

        ring8 = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]

        def true_junctions(pset):
            out = set()
            for (x, y) in pset:
                bits = [((x + dx, y + dy) in pset) for dx, dy in ring8]
                if sum(1 for i in range(8) if (not bits[i]) and bits[(i + 1) % 8]) >= 3:
                    out.add((x, y))
            return out

        nb_full = neighbours(pts)
        full_pts = set(pts)
        nb = nb_full
        # Spur pruning (TOPOLOGY COUNTS ONLY; the centerline path below uses the unpruned skeleton): a raster skeleton grows tiny side twigs on stair-stepped edges; without pruning they
        # count as real branches and make results depend on image resolution. Remove end-branches that reach
        # a junction within `min_spur_px` (about the local half-width).
        for _pass in range(2):
            if min_spur_px <= 0:
                break
            ends = [p for p, v in nb.items() if len(v) == 1]
            removed = False
            for e in ends:
                chain, prev, cur = [e], None, e
                while len(chain) <= min_spur_px:
                    nxt = [q for q in nb[cur] if q != prev and q not in chain]
                    if len(nxt) != 1:
                        break
                    prev, cur = cur, nxt[0]
                    if cur in true_junctions(pts):  # reached a real junction (crossing number >= 3): chain is a spur
                        for c in chain:
                            pts.discard(c)
                        removed = True
                        break
                    chain.append(cur)
            if not removed:
                break
            nb = neighbours(pts)
        if not pts:
            return [], {"endpoints": 0, "branch_points": 0, "closed_loop": False}
        endpoints = sum(1 for v in nb.values() if len(v) == 1)
        # A junction is a pixel whose 8-neighbour ring has >=3 separate runs
        # (crossing number); naive "neighbours >= 3" counts every staircase pixel.
        ring = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
        junction = set()
        for (x, y) in pts:
            bits = [((x + dx, y + dy) in pts) for dx, dy in ring]
            cn = sum(1 for i in range(8) if (not bits[i]) and bits[(i + 1) % 8])
            if cn >= 3:
                junction.add((x, y))
        seen, branch = set(), 0
        for j in junction:  # cluster adjacent junction pixels into one branch point
            if j in seen:
                continue
            branch += 1
            stack = [j]
            while stack:
                c = stack.pop()
                if c in seen:
                    continue
                seen.add(c)
                stack += [n for n in nb[c] if n in junction and n not in seen]

        def bfs(src):
            prev = {src: None}
            order = [src]
            for cur in order:
                for n in nb_full[cur]:
                    if n not in prev:
                        prev[n] = cur
                        order.append(n)
            return order[-1], prev

        a, _ = bfs(next(iter(full_pts)))
        b, prev = bfs(a)
        path = []
        cur = b
        while cur is not None:
            path.append(cur)
            cur = prev[cur]
        return path, {"endpoints": endpoints, "branch_points": branch, "closed_loop": endpoints == 0 and branch == 0}

    @staticmethod
    def _polyline_len(path) -> float:
        return float(sum(((path[i][0] - path[i + 1][0]) ** 2 + (path[i][1] - path[i + 1][1]) ** 2) ** 0.5
                         for i in range(len(path) - 1)))

    @staticmethod
    def _confidence(area_px: float, perimeter_px: float, vertex_count: int) -> Confidence:
        """A geometric proxy for detection confidence — NOT a trained model's
        probability. Rewards larger regions with a reasonably simple outline;
        penalises tiny slivers and very high vertex counts."""
        if perimeter_px <= 0:
            return Confidence(0.3, "degenerate contour (zero perimeter); geometric proxy")

        compactness = min(1.0, (4 * 3.14159265 * max(area_px, 0.0)) / (perimeter_px**2) * 1.6)
        size_factor = min(1.0, area_px / 4000.0)
        jaggedness_penalty = 0.0 if vertex_count <= 40 else min(0.3, (vertex_count - 40) / 200.0)

        value = max(0.1, min(0.95, 0.4 + 0.35 * compactness + 0.25 * size_factor - jaggedness_penalty))
        reason = (
            f"geometric proxy: area={area_px:.0f}px, compactness={compactness:.2f}, "
            f"outline points={vertex_count} — not a trained detector's confidence"
        )
        return Confidence(value, reason)


def export_debug(blueprint: ObjectBlueprint, out_dir: str) -> list[str]:
    """Write numbered debug artifacts (subset of spec section 50 that Vision V2
    actually produces). Returns the written paths."""
    import json
    import os

    import cv2
    import numpy as np

    os.makedirs(out_dir, exist_ok=True)
    di = blueprint.debug_images
    written: list[str] = []

    def save(name, img):
        path = os.path.join(out_dir, name)
        cv2.imwrite(path, img)
        written.append(path)

    if "original" in di:
        save("01_original.png", di["original"])
    if "background_mask" in di:
        save("02_background_mask.png", di["background_mask"])
    if "segmentation" in di:
        save("03_segmentation.png", di["segmentation"])
    if "original" in di:
        h, w = di["original"].shape[:2]
        sx, sy = w / blueprint.target_size_mm[0], h / blueprint.target_size_mm[1]
        cont = np.full((h, w, 3), 255, np.uint8)
        cl = cont.copy()
        for o in blueprint.objects:
            f = lambda pts: np.array([[p[0] * sx, p[1] * sy] for p in pts], np.int32)  # noqa: E731
            cv2.polylines(cont, [f(o.geometry)], True, (0, 0, 200), 1)
            for hpts in o.holes_mm:
                cv2.polylines(cont, [f(hpts)], True, (200, 0, 0), 1)
            if o.centerline_mm:
                cv2.polylines(cl, [f(o.centerline_mm)], False, (0, 140, 0), 1)
                cv2.polylines(cl, [f(o.geometry)], True, (200, 200, 200), 1)
        save("04_contours.png", cont)
        save("05_centerlines.png", cl)
    path = os.path.join(out_dir, "06_object_blueprint.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(blueprint.to_dict(), fh, indent=2)
    written.append(path)
    return written
