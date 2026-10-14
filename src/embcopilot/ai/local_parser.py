"""Offline rule-based parser (English + Hinglish) -> raw step dicts in the SAME schema the LLM must produce.
Works with no network and no keys.  Deterministic; never executes anything."""
from __future__ import annotations

import re

FABRIC_WORDS = [("STRETCH_FABRIC", r"stretch|lycra|spandex|silk"), ("PIQUE", r"piqu[eé]|polo"), ("DENIM", r"denim|jeans|jacket|heavy"),
                ("T_SHIRT", r"t[- ]?shirt|tee\b|knit"), ("JERSEY", r"jersey"), ("TOWEL", r"towel|terry"), ("TWILL", r"twill|woven"),
                ("CANVAS", r"canvas"), ("FLEECE", r"fleece"), ("CAP", r"\bcap\b|hat"), ("PATCH", r"patch")]
COLORS = {"gold": "#C9A227", "golden": "#C9A227", "sunehri": "#C9A227", "black": "#000000", "kala": "#000000", "white": "#FFFFFF", "safed": "#FFFFFF",
          "red": "#C8102E", "lal": "#C8102E", "blue": "#1F4E9E", "neela": "#1F4E9E", "green": "#2E7D32", "hara": "#2E7D32", "silver": "#C0C0C0"}
TARGETS = [("monogram", r"monogram|\blv\b|central logo|center logo|centre logo|emblem|\blogo\b"), ("inner_ring", r"inner (ring|circle|border)|andar ki ring"),
           ("outer_ring", r"outer (ring|circle|border)|bahar ki ring|bahari|\bborder\b|\bring\b"), ("text", r"\btext\b|lettering|letters|akshar"),
           ("background", r"background|\bbg\b|backdrop")]
STITCH_WORDS = [("input_c", r"input[- ]?c\b|column[- ]?c\b"), ("triple_run", r"triple[- ]?run"), ("tatami", r"tatami|\bfill\b"), ("puff", r"\bpuff\b|3d"),
                ("applique", r"appliqu[eé]"), ("cross_stitch", r"cross[- ]?stitch"), ("satin", r"satin"), ("run", r"\brun\b(?!\s*underlay)")]
UNDERLAY_WORDS = [("double_zigzag", r"double[- ]?zig[- ]?zag"), ("zigzag", r"zig[- ]?zag"), ("edge_run", r"edge[- ]?run|edge[- ]?walk"),
                  ("center_run", r"cent(?:er|re)[- ]?run|center[- ]?walk"), ("tatami", r"tatami underlay")]
SPLIT = re.compile(r"\s*(?:,|;|\.(?=\s|$)|\band\b|\baur\b|\bthen\b|\bphir\b|\bfir\b)\s*(?![^()]*\))", re.I)
NUM = r"(\d+(?:\.\d+)?)"


def _target(clause: str, prev: str | None):
    m = re.search(r"(satin|tatami|run)\s+(?:columns?|objects?|areas?|fills?)", clause)
    if m and re.search(r"\bconvert|change|badal", clause):
        return f"type:{m.group(1)}"
    for name, pat in TARGETS:
        if re.search(pat, clause):
            return name
    return prev


def parse_local(text: str) -> dict:
    steps: list[dict] = []
    unknown: list[str] = []
    prev = None
    low_all = text.lower()
    for raw in [c.strip() for c in SPLIT.split(low_all) if c and c.strip()]:
        c = raw
        tgt = _target(c, prev)
        made = False
        src = {"source_text": raw}
        if re.search(r"\bpreserve|\bkeep\b|\bprotect|\bdon'?t touch|\bnot change", c) and tgt:
            steps.append({"op": "protect", "target": tgt, **src}); made = True
            continue                      # a 'preserve X' clause must not become the implicit target of later clauses
        if re.search(r"\b(delete|remove|hata\w*|nikal\w*|mita\w*|exclude)\b", c) and tgt and not re.search(r"tiny|short|stitch", c):
            steps.append({"op": "delete_object", "target": tgt, **src}); made = True
        # fabric
        if re.search(r"fabric|preset|profile|denim|piqu|polo|towel|jersey|twill|canvas|fleece|\bcap\b|patch|t[- ]?shirt|stretch|\blagao\b", c):
            for key, pat in FABRIC_WORDS:
                if re.search(pat, c):
                    steps.append({"op": "set_fabric", "fabric": key, **src}); made = True
                    break
        # stitch type (+ width)
        st = next((k for k, p in STITCH_WORDS if re.search(p, c)), None)
        wm = re.search(NUM + r"\s*mm(?!\s*(?:spacing|density|pitch|pull))", c)
        want_stitch = st and re.search(r"convert|change|make|use|set|karo|kar do|border|ring|column|\binput\b|to\b", c) and not re.search(r"underlay", c)
        if want_stitch and (tgt or True):
            if tgt is None:
                unknown.append(raw + "  [which object? add a target such as 'outer ring']")
                continue
            # "convert satin columns to tatami": the NEW type is the one after 'to'
            m2 = re.search(r"\bto\s+(?:a\s+)?(\w[\w\- ]*)", c)
            if m2 and tgt.startswith("type:"):
                st = next((k for k, p in STITCH_WORDS if re.search(p, m2.group(1))), st)
            s = {"op": "set_stitch_type", "target": tgt, "stitch_type": st, **src}
            if wm and not re.search(r"spacing|pitch|density", c[wm.end():wm.end() + 12]):
                s["width_mm"] = float(wm.group(1))
            steps.append(s); made = True
        elif wm and tgt and re.search(r"width|wide|thick|\bmm\b", c) and not re.search(r"spacing|density|pitch|pull|split|stitch length", c):
            steps.append({"op": "set_width", "target": tgt, "width_mm": float(wm.group(1)), **src}); made = True
        # density / spacing
        m = re.search(NUM + r"\s*mm\s*(?:spacing|density|pitch)", c) or re.search(r"(?:spacing|density|pitch)\s*(?:of|to|=|:)?\s*" + NUM + r"\s*mm", c)
        if m:
            steps.append({"op": "set_density", "target": tgt or "all", "density_mm": float(m.group(1)), **src}); made = True
        # pull comp
        if re.search(r"pull[- ]?comp\w*|push[- ]?pull", c):
            mm = re.search(NUM + r"\s*mm", c); pc = re.search(NUM + r"\s*%", c)
            if pc:
                steps.append({"op": "set_pull_comp", "target": tgt or "all", "pull_comp_pct": float(pc.group(1)), **src}); made = True
            elif mm:
                steps.append({"op": "set_pull_comp", "target": tgt or "all", "pull_comp_mm": float(mm.group(1)), **src}); made = True
        # auto split / max stitch
        if re.search(r"auto[- ]?split|split|max(?:imum)? stitch|longer than|above|over", c) and re.search(r"stitch|split", c):
            m = re.search(NUM + r"\s*mm", c)
            if m and not re.search(r"pull|spacing|pitch", c):
                steps.append({"op": "auto_split", "max_stitch_mm": float(m.group(1)), **src}); made = True
        if re.search(r"tiny|min(?:imum)? stitch|short stitch", c):
            m = re.search(NUM + r"\s*mm", c)
            steps.append({"op": "min_stitch", "min_stitch_mm": float(m.group(1)) if m else 0.4, **src}); made = True
        # underlay
        if re.search(r"underlay", c):
            if re.search(r"no underlay|without underlay|remove underlay|underlay (?:off|none)", c):
                ul = ["none"]
            else:
                # strip "tatami underlay" first so "tatami" alone is not read as a stitch change
                ul = [k for k, p in UNDERLAY_WORDS if re.search(p, c)]
            if ul:
                steps.append({"op": "set_underlay", "target": tgt or "all", "underlay": ul, **src}); made = True
        # angle
        m = re.search(r"angle\D{0,12}" + NUM + r"\s*(?:°|deg)?", c)
        if m and not re.search(r"underlay", c):
            steps.append({"op": "set_angle", "target": tgt or "all", "angle_deg": float(m.group(1)), **src}); made = True
        # colour
        col = next((v for k, v in COLORS.items() if re.search(rf"\b{k}\b", c)), None)
        if col and tgt and not re.search(r"delete|remove", c):
            steps.append({"op": "set_color", "target": tgt, "color_hex": col, **src}); made = True
        # optimise
        if re.search(r"(reduc|fewer|less|minimi[sz]|kam)\w*[^,]*\btrims?\b|\btrims?\b[^,]*(reduc|fewer|kam)", c):
            steps.append({"op": "optimize", "what": ["fewer_trims"], **src}); made = True
        if re.search(r"(optimi[sz]e|reduc|kam|fix|shorten)\w*[^,]*\b(jumps?|travel|connectors?|path)\b", c):
            steps.append({"op": "optimize", "what": ["travel"], **src}); made = True
        # export / emb
        m = re.search(r"\b(?:export|save|download)\b[^,]*?\b(dst|exp|pes|jef|emb)\b", c)
        if m:
            steps.append({"op": "export", "format": m.group(1), **src}); made = True
        elif re.search(r"\bemb\b", c):
            steps.append({"op": "emb_info", **src}); made = True
        if re.fullmatch(r"(run |do )?qc|check quality|quality check", c):
            steps.append({"op": "qc", **src}); made = True
        if tgt:
            prev = tgt
        if not made:
            unknown.append(raw)
    return {"steps": steps, "unrecognised": unknown}
