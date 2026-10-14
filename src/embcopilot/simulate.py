"""Embroidery preview (Phase 11). EVERYTHING here is labelled SIMULATION: a
flat 2-D render of the generated stitch commands, not a physical thread
simulation. Modes: flat (cover only), stitch-path (all parts, connectors
marked), thread-rendered (thick shaded lines), per-object. Plus a
self-contained HTML player (play/pause/step/speed/object list) driven by the
real stitch list."""

from __future__ import annotations

import json
import math

import numpy as np

from .stitch import COLOR_CHANGE, END, JUMP, STITCH, TRIM, StitchPlan, _rgb


def render(plan: StitchPlan, mode: str = "path", px_per_mm: float = 8.0, upto: int | None = None):
    import cv2

    W, H = plan.canvas_mm
    img = np.full((int(H * px_per_mm) + 8, int(W * px_per_mm) + 8, 3), 248, np.uint8)
    P = lambda x, y: (int(round(x * px_per_mm)) + 4, int(round(y * px_per_mm)) + 4)  # noqa: E731
    prev = None
    sts = plan.stitches[:upto] if upto is not None else plan.stitches
    for s in sts:
        if s.command == STITCH and prev is not None and prev.command in (STITCH, JUMP, COLOR_CHANGE):
            r, g, b = _rgb(s.color)
            col = (b, g, r)
            under = s.part.startswith("underlay")
            hidden = s.part.startswith("connector")
            if mode == "flat" and (under or hidden):
                pass
            else:
                if mode == "thread":
                    cv2.line(img, P(prev.x, prev.y), P(s.x, s.y), tuple(int(c * 0.55) for c in col), max(2, int(0.55 * px_per_mm)), cv2.LINE_AA)
                    cv2.line(img, P(prev.x, prev.y), P(s.x, s.y), col, max(1, int(0.35 * px_per_mm)), cv2.LINE_AA)
                else:
                    c2 = tuple(int(v * 0.5 + 255 * 0.5) for v in col) if under else col
                    cv2.line(img, P(prev.x, prev.y), P(s.x, s.y), c2, 1, cv2.LINE_AA)
        elif s.command == JUMP and prev is not None and mode == "path":
            cv2.line(img, P(prev.x, prev.y), P(s.x, s.y), (200, 120, 0), 1, cv2.LINE_AA)
        elif s.command == TRIM and mode == "path":
            cv2.drawMarker(img, P(s.x, s.y), (0, 0, 220), cv2.MARKER_TILTED_CROSS, 10, 2)
        if s.command != TRIM:
            prev = s
    cv2.putText(img, f"SIMULATION ({mode}) - not a physical thread model", (6, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (90, 90, 90), 1, cv2.LINE_AA)
    return img


def render_object(plan: StitchPlan, object_id: str, px_per_mm: float = 8.0):
    sub = StitchPlan(plan.canvas_mm, [s for s in plan.stitches if s.object_id in (object_id, "")], plan.order)
    return render(sub, "thread", px_per_mm)


def html_player(plan: StitchPlan, objects=None, title="Embroidery Copilot - stitch playback (SIMULATION)") -> str:
    data = [[round(s.x, 2), round(s.y, 2), s.command[0], s.color, s.object_id[-4:], s.part[:1]] for s in plan.stitches]
    names = {o.object_id[-4:]: f"{o.name} [{o.category}]" for o in (objects or [])}
    st = plan.stats()
    return f"""<!doctype html><meta charset=utf-8><title>{title}</title>
<style>body{{font:14px sans-serif;margin:16px}}canvas{{border:1px solid #999;background:#f8f8f8}}button{{margin:2px}}</style>
<h3>{title}</h3><p><b>SIMULATION</b> — flat render of the generated stitch commands. Numbers below are computed from those commands; time/thread are estimates (see JSON assumptions).</p>
<canvas id=c width={int(plan.canvas_mm[0]*6)} height={int(plan.canvas_mm[1]*6)}></canvas><br>
<button id=play>Play</button><button id=prev>Prev</button><button id=next>Next</button>
Speed <input id=spd type=range min=1 max=200 value=40> <span id=info></span><br>
<input id=pos type=range min=0 max={len(data)} value=0 style="width:{int(plan.canvas_mm[0]*6)}px">
<pre>{json.dumps({k:v for k,v in st.items() if k!='ESTIMATE_ASSUMPTIONS'})}</pre>
<div id=obj></div>
<script>
const D={json.dumps(data)},N={json.dumps(names)},S=6,c=document.getElementById('c'),x=c.getContext('2d');
let i=0,t=null;function col(s){{const m=s.match(/\\d+/g);return m?`rgb(${{m[0]}},${{m[1]}},${{m[2]}})`:'#888'}}
function draw(n){{x.clearRect(0,0,c.width,c.height);let p=null;for(let k=0;k<n;k++){{const d=D[k];
if(d[2]=='S'&&p){{x.strokeStyle=col(d[3]);x.lineWidth=d[5]=='u'?.5:1;x.globalAlpha=d[5]=='u'?.5:1;x.beginPath();x.moveTo(p[0]*S,p[1]*S);x.lineTo(d[0]*S,d[1]*S);x.stroke()}}
else if(d[2]=='J'&&p){{x.globalAlpha=.6;x.strokeStyle='#c87800';x.beginPath();x.moveTo(p[0]*S,p[1]*S);x.lineTo(d[0]*S,d[1]*S);x.stroke()}}
if(d[2]!='T')p=d}}x.globalAlpha=1;const d=D[Math.max(0,n-1)]||[];document.getElementById('info').textContent=n+' / '+D.length+'  '+(N[d[4]]||'');document.getElementById('pos').value=n}}
function set(n){{i=Math.max(0,Math.min(D.length,n));draw(i)}}
play.onclick=()=>{{if(t){{clearInterval(t);t=null;play.textContent='Play'}}else{{play.textContent='Pause';t=setInterval(()=>{{set(i+Number(spd.value));if(i>=D.length){{clearInterval(t);t=null;play.textContent='Play'}}}},30)}}}};
prev.onclick=()=>set(i-Number(spd.value));next.onclick=()=>set(i+Number(spd.value));pos.oninput=()=>set(Number(pos.value));
document.getElementById('obj').innerHTML='<b>Objects</b><ul>'+Object.values(N).map(v=>'<li>'+v+'</li>').join('')+'</ul>';set(D.length)
</script>"""
