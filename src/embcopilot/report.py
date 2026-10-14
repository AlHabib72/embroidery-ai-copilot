"""Render an Analysis as a self-contained HTML dashboard, or as JSON."""

from __future__ import annotations

import html
import json
import os

from .models import Analysis, Design, StitchKind
from .rules import severity_counts

PALETTE = [
    "#e8dcc4", "#9bb168", "#6f8f3e", "#3f5e2a",
    "#e79ab0", "#c0395a", "#2b2b2b", "#f2f2f2",
    "#5b8fd6", "#d68c3f", "#8a5bd6", "#3fb6a8",
]

SEV = {
    "critical": ("#ff5c72", "Critical"),
    "high": ("#ff9f45", "High"),
    "medium": ("#ffd45c", "Medium"),
    "low": ("#5ccfa0", "Low"),
}


# ----------------------------------------------------------------------
# JSON
# ----------------------------------------------------------------------


def render_json(a: Analysis, out_path: str) -> str:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(a.to_dict(), fh, indent=2)
    return out_path


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------


def _stitch_svg(design: Design, width: int = 620, highlight: tuple | None = None) -> str:
    x0, y0, x1, y1 = design.bounds_mm()
    w = max(1e-6, x1 - x0)
    h = max(1e-6, y1 - y0)
    pad = 6
    scale = (width - 2 * pad) / w
    height = int(h * scale + 2 * pad)

    def px(rx: float, ry: float) -> tuple[float, float]:
        return (rx - x0) * scale + pad, (y1 - ry) * scale + pad

    paths: list[str] = []
    jumps: list[str] = []
    color = 0
    run: list[str] = []
    prev_pos: tuple[float, float] | None = None

    for r in design.records:
        if r.kind == StitchKind.COLOR_CHANGE:
            if run:
                paths.append(f'<path d="{" ".join(run)}" stroke="{PALETTE[color % len(PALETTE)]}"/>')
                run = []
            color += 1
            prev_pos = None
        elif r.kind == StitchKind.JUMP:
            if run:
                paths.append(f'<path d="{" ".join(run)}" stroke="{PALETTE[color % len(PALETTE)]}"/>')
                run = []
            cur_pos = px(r.x / 10.0, r.y / 10.0)
            if prev_pos is not None:
                ax, ay = prev_pos
                bx, by = cur_pos
                jumps.append(f"M{ax:.1f},{ay:.1f}L{bx:.1f},{by:.1f}")
            prev_pos = cur_pos
        elif r.kind == StitchKind.STITCH:
            cx, cy = px(r.x / 10.0, r.y / 10.0)
            run.append(f"M{cx:.1f},{cy:.1f}" if not run else f"L{cx:.1f},{cy:.1f}")
            prev_pos = (cx, cy)

    if run:
        paths.append(f'<path d="{" ".join(run)}" stroke="{PALETTE[color % len(PALETTE)]}"/>')

    jump_layer = ""
    if jumps:
        jump_layer = f'<g class="jump"><path d="{" ".join(jumps)}"/></g>'

    highlight_layer = ""
    if highlight:
        hx0, hy0, hx1, hy1 = highlight
        ax, ay = px(hx0, hy1)
        bx, by = px(hx1, hy0)
        rw, rh = max(2.0, bx - ax), max(2.0, by - ay)
        highlight_layer = (
            f'<rect class="hl" x="{ax - 3:.1f}" y="{ay - 3:.1f}" '
            f'width="{rw + 6:.1f}" height="{rh + 6:.1f}" rx="3"/>'
        )

    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="Stitch path preview">'
        f'<g class="stitches" fill="none" stroke-width="0.7" '
        f'stroke-linecap="round" stroke-linejoin="round">{"".join(paths)}</g>'
        f"{jump_layer}{highlight_layer}</svg>"
    )


def render_html(a: Analysis, design: Design, out_path: str) -> str:
    e = html.escape
    x0, y0, x1, y1 = design.bounds_mm()
    counts = severity_counts(a.issues)

    metrics = [
        ("Reconstructed segments", f"{len(a.segments)}", "inferred, not native Wilcom object count"),
        ("Stitches", f"{a.stitches:,}", f"{a.stitch_length_avg_mm:.2f} mm average"),
        ("Colours", f"{a.colors}", f"{max(0, a.colors - 1)} changes"),
        ("Estimated trims", f"{a.estimated_trims}", f"theoretical minimum ~{a.trims_theoretical}"),
        ("Jumps", f"{a.jumps}", f"~{a.travel_actual_mm:.0f} mm estimated travel"),
        ("Estimated run time", f"{a.runtime_minutes:.1f} min", f"at {a.config_used.get('spm', '?')} spm"),
        ("Estimated thread", f"{a.thread_metres:.1f} m", f"~{a.bobbin_metres:.1f} m bobbin"),
        ("Size", f"{a.width_mm:.1f} \u00d7 {a.height_mm:.1f}", "mm"),
    ]
    metric_html = "".join(
        f'<div class="metric"><span class="mlabel">{e(l)}</span>'
        f'<span class="mvalue">{e(v)}</span>'
        f'<span class="mnote">{e(n)}</span></div>'
        for l, v, n in metrics
    )

    sev_html = "".join(
        f'<div class="sevcard" style="--c:{SEV[k][0]}"><span class="sevn">{counts[k]}</span>'
        f'<span class="sevl">{SEV[k][1]}</span></div>'
        for k in ("critical", "high", "medium", "low")
    )

    if a.issues:
        issue_html = "".join(
            f'<li class="issue" style="--sev:{SEV[i.severity][0]}">'
            f'<div class="ihead"><span class="ibadge">{SEV[i.severity][1]}</span>'
            f"<h3>{e(i.title)}</h3></div>"
            f"<p>{e(i.detail)}</p>"
            + (f'<p class="why"><b>Why it matters:</b> {e(i.why_it_matters)}</p>' if i.why_it_matters else "")
            + (f'<p class="rec"><b>Recommended review:</b> {e(i.recommendation)}</p>' if i.recommendation else "")
            + (f'<p class="isave">Recovers {e(i.saving)}</p>' if i.saving else "")
            + (f'<p class="loc">Location: x {i.bbox[0]:.1f}\u2013{i.bbox[2]:.1f} mm, y {i.bbox[1]:.1f}\u2013{i.bbox[3]:.1f} mm</p>' if i.bbox else "")
            + "</li>"
            for i in a.issues
        )
    else:
        issue_html = (
            '<li class="issue clean"><div class="ihead"><h3>Nothing to flag</h3>'
            "</div><p>Estimated trims, travel, stitch lengths and density are all "
            "inside the configured limits for this fabric preset.</p></li>"
        )

    rows = "".join(
        f'<tr><td><span class="chip" style="background:'
        f'{PALETTE[(c["index"] - 1) % len(PALETTE)]}"></span>{c["index"]}</td>'
        f'<td class="num">{c["stitches"]:,}</td>'
        f'<td class="num">{c["segments"]}</td>'
        f'<td class="num">{c["share_pct"]}%</td></tr>'
        for c in a.color_blocks
    )

    cut = 0
    if a.travel_actual_mm > 0:
        cut = int(round(100 * (a.travel_actual_mm - a.travel_theoretical_mm) / a.travel_actual_mm))

    first_issue_bbox = next((i.bbox for i in a.issues if i.bbox), None)

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(a.name)} \u2014 DST QC analysis</title>
<style>
:root{{
  --bg:#13121c; --panel:#1b192a; --panel2:#211e33; --line:#302c47;
  --ink:#e8e5f7; --muted:#8e88ad; --accent:#7c5cff;
  --ok:#5ccfa0;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 "Inter","Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 60px}}
header{{display:flex;flex-wrap:wrap;gap:18px;align-items:flex-end;
  justify-content:space-between;margin-bottom:14px}}
h1{{font-size:26px;font-weight:650;margin:0 0 4px;letter-spacing:-.01em}}
.sub{{color:var(--muted);font-size:13px;margin:0}}
.score{{display:flex;align-items:center;gap:14px;background:var(--panel);
  border:1px solid var(--line);border-radius:14px;padding:12px 18px}}
.sring{{width:54px;height:54px;border-radius:50%;display:grid;place-items:center;
  font-weight:700;font-size:17px;
  background:conic-gradient(var(--accent) calc({a.score}*1%),#2a2740 0)}}
.sring i{{width:42px;height:42px;border-radius:50%;background:var(--panel);
  display:grid;place-items:center;font-style:normal}}
.grade{{font-size:13px;color:var(--muted)}}
.grade b{{display:block;color:var(--ink);font-size:15px;font-weight:600}}
.disclaimer{{font-size:12px;color:var(--muted);background:var(--panel2);
  border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:18px}}

.sevrow{{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:14px;overflow:hidden;margin-bottom:14px}}
.sevcard{{background:var(--panel);padding:12px 14px;display:flex;flex-direction:column;
  gap:2px;border-top:3px solid var(--c)}}
.sevn{{font-size:22px;font-weight:700;color:var(--c)}}
.sevl{{font-size:12px;color:var(--muted)}}

.metrics{{display:grid;gap:1px;background:var(--line);border:1px solid var(--line);
  border-radius:14px;overflow:hidden;
  grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin-bottom:22px}}
.metric{{background:var(--panel);padding:14px 16px;display:flex;flex-direction:column;gap:2px}}
.mlabel{{font-size:12px;color:var(--muted)}}
.mvalue{{font-size:21px;font-weight:650;font-variant-numeric:tabular-nums;
  letter-spacing:-.01em}}
.mnote{{font-size:11.5px;color:var(--muted)}}

.cols{{display:grid;gap:18px;grid-template-columns:1.25fr .95fr;align-items:start}}
@media(max-width:900px){{.cols{{grid-template-columns:1fr}}}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:16px 18px}}
.panel h2{{font-size:14px;font-weight:600;margin:0 0 12px;color:var(--muted)}}
.preview{{background:var(--panel2);border-radius:10px;padding:8px;overflow:auto}}
.jump path{{stroke:#ff5c72;stroke-width:.6;stroke-dasharray:2 3;fill:none;opacity:.75}}
.hl{{fill:none;stroke:#7c5cff;stroke-width:1.4;stroke-dasharray:3 2}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;color:var(--muted);margin-top:10px}}
.legend s{{display:inline-block;width:16px;height:2px;background:#ff5c72;
  vertical-align:middle;margin-right:6px;text-decoration:none}}
.legend em{{display:inline-block;width:12px;height:12px;border:1.4px dashed #7c5cff;
  border-radius:2px;vertical-align:-2px;margin-right:6px;font-style:normal}}
.zoomrow{{display:flex;gap:6px;margin-bottom:8px}}
.zoomrow button{{background:var(--panel2);border:1px solid var(--line);color:var(--ink);
  border-radius:6px;padding:3px 10px;font-size:12px;cursor:pointer}}
.zoomrow button:hover{{border-color:var(--accent)}}

ul.issues{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}}
.issue{{border:1px solid var(--line);border-left:3px solid var(--sev,var(--ok));
  border-radius:10px;padding:12px 14px;background:var(--panel2)}}
.ihead{{display:flex;align-items:center;gap:9px;margin-bottom:5px}}
.issue h3{{font-size:14.5px;font-weight:600;margin:0}}
.ibadge{{font-size:10.5px;font-weight:600;color:var(--sev);
  border:1px solid var(--sev);border-radius:20px;padding:1px 8px}}
.issue p{{margin:6px 0 0;font-size:13px;color:#b9b4d4}}
.issue p:first-of-type{{margin-top:0}}
.issue .why,.issue .rec{{color:#a49fc4}}
.isave{{color:var(--ok)!important;font-weight:550}}
.loc{{font-family:ui-monospace,monospace;font-size:11.5px!important;color:#8e88ad!important}}
.clean{{--sev:var(--ok)}}

table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;font-weight:500;color:var(--muted);font-size:12px;
  padding:0 0 8px}}
td{{padding:7px 0;border-top:1px solid var(--line);font-variant-numeric:tabular-nums}}
.num{{text-align:right}}
.chip{{display:inline-block;width:11px;height:11px;border-radius:3px;
  margin-right:9px;vertical-align:-1px;border:1px solid rgba(255,255,255,.18)}}

.bars{{margin-top:6px}}
.bar{{margin-bottom:12px}}
.bar span{{display:flex;justify-content:space-between;font-size:12.5px;
  color:var(--muted);margin-bottom:5px}}
.bar span b{{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums}}
.track{{height:7px;border-radius:4px;background:#2a2740;overflow:hidden}}
.fill{{height:100%;border-radius:4px}}
.note{{font-size:12.5px;color:var(--muted);margin:14px 0 0}}
footer{{margin-top:28px;font-size:12px;color:var(--muted)}}
</style></head><body><div class="wrap">

<header>
  <div>
    <h1>{e(a.name)}</h1>
    <p class="sub">{e(a.source)} \u00b7 hoop area {x0:.1f},{y0:.1f} to {x1:.1f},{y1:.1f} mm
      \u00b7 fabric preset: {e(a.fabric_preset)}</p>
  </div>
  <div class="score">
    <div class="sring"><i>{a.score}</i></div>
    <div class="grade">QC Heuristic Score<b>{e(a.grade)}</b></div>
  </div>
</header>

<p class="disclaimer">This score is a software heuristic based on rule
violations detected in the stitch data. It is not a certification of
stitch-out quality, and every figure on this page is an estimate derived
from geometry \u2014 not a measurement taken from a real machine, thread spool,
or fabric. "Reconstructed segments" approximate objects; DST does not store
Wilcom's native object boundaries. Analyzer v{e(a.analyzer_version)}.</p>

<div class="sevrow">{sev_html}</div>

<div class="metrics">{metric_html}</div>

<div class="cols">
  <section class="panel">
    <h2>Stitch path</h2>
    <div class="preview">{_stitch_svg(design, highlight=first_issue_bbox)}</div>
    <p class="legend"><span><s></s>jump / travel</span>
       <span><em></em>most severe issue location</span>
       <span>solid lines are needle penetrations, coloured by thread block</span></p>
  </section>

  <section class="panel">
    <h2>What needs review ({len(a.issues)})</h2>
    <ul class="issues">{issue_html}</ul>
  </section>
</div>

<div class="cols" style="margin-top:18px">
  <section class="panel">
    <h2>Colour sequence</h2>
    <table><thead><tr><th>Block</th><th class="num">Stitches</th>
      <th class="num">Segments</th><th class="num">Share</th></tr></thead>
      <tbody>{rows}</tbody></table>
  </section>

  <section class="panel">
    <h2>Theoretical re-sequencing preview</h2>
    <div class="bars">
      <div class="bar"><span>Estimated travel now <b>{a.travel_actual_mm:.0f} mm</b></span>
        <div class="track"><div class="fill" style="width:100%;background:#ff5c72"></div></div></div>
      <div class="bar"><span>Theoretical travel <b>{a.travel_theoretical_mm:.0f} mm</b></span>
        <div class="track"><div class="fill" style="width:{max(2, 100 - cut)}%;background:var(--ok)"></div></div></div>
      <div class="bar"><span>Estimated trims <b>{a.estimated_trims} &rarr; {a.trims_theoretical}</b></span>
        <div class="track"><div class="fill" style="width:{max(2, int(100 * a.trims_theoretical / max(1, a.estimated_trims)))}%;background:var(--accent)"></div></div></div>
    </div>
    <p class="note">Nearest-neighbour ordering inside each colour block, segments
    free to sew in either direction, greedy (not a guaranteed-optimal route).
    Colour order is left untouched. This is a preview of what looks
    recoverable \u2014 no file has been rewritten.</p>
  </section>
</div>

<footer>Embroidery AI Copilot &middot; Phase 1 analyser v{e(a.analyzer_version)}
&middot; read-only, the source DST is never modified &middot; generated {e(a.generated_at)}</footer>
</div></body></html>"""

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return out_path


# Backwards-compatible alias used by older call sites / examples.
render = render_html
