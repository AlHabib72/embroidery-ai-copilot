"""Static HTML report for the demo (SIMULATION-labelled)."""

from __future__ import annotations

import base64
import html


def _b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def write_demo_report(res, path: str, profile: str) -> None:
    import os

    out = os.path.dirname(path)
    imgs = "".join(
        f'<figure style="display:inline-block;margin:6px"><img width=300 src="data:image/png;base64,{_b64(os.path.join(out, n))}"><figcaption>{html.escape(cap)}</figcaption></figure>'
        for n, cap in [("reference.png", "Reference"), ("03_segmentation.png", "Segmentation"),
                       ("11_simulation.png", "SIMULATION: stitch path"), ("11b_simulation_thread.png", "SIMULATION: thread-rendered")]
        if os.path.exists(os.path.join(out, n)))
    rows = ""
    for o, a in zip(res.objects, res.assessments):
        st = res.plan.object_status.get(o.object_id, {})
        cmp_ = next((c.status for c in res.comparison if c.object_id == o.object_id), "-")
        rows += (f"<tr><td>{html.escape(o.name)}</td><td>{o.category}</td><td>{o.confidence.get('semantic').value if 'semantic' in o.confidence else '-'}</td>"
                 f"<td>{st.get('strategy')}</td><td>{cmp_}</td><td>{a.risk}</td><td>{html.escape('; '.join(a.reasons)) or '-'}</td></tr>")
    finds = "".join(f"<li>{f.severity} {f.code}: {html.escape(f.message)}</li>" for f in res.validation.findings)
    stats = {k: v for k, v in res.plan.stats().items() if k != "ESTIMATE_ASSUMPTIONS"}
    open(path, "w", encoding="utf-8").write(f"""<!doctype html><meta charset=utf-8><title>Embroidery Copilot demo</title>
<style>body{{font:14px sans-serif;margin:20px}}td,th{{border:1px solid #bbb;padding:4px 8px}}table{{border-collapse:collapse}}</style>
<h2>Embroidery AI Copilot - demo report (profile: {profile})</h2>
<p><b>Status:</b> stitch geometry is REAL (generated + validated); previews are SIMULATION; approval is REQUIRED for every object below
(nothing was sent to Wilcom; no EMB file is produced).</p>{imgs}
<h3>Objects</h3><table><tr><th>Object</th><th>Semantic (heuristic)</th><th>Sem. conf.</th><th>Stitch</th><th>Reference match</th><th>Risk</th><th>Why human review</th></tr>{rows}</table>
<h3>Stats (computed; time and thread are estimates)</h3><pre>{html.escape(str(stats))}</pre>
<h3>Validation: {res.validation.status}</h3><ul>{finds}</ul>
<p>Interactive playback: <a href="playback.html">playback.html</a> (SIMULATION).</p>""")
