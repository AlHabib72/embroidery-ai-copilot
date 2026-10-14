# Embroidery AI Copilot v0.7.0

> **v0.7 focus: AI Prompt Box + EMB handling.** Natural language (English/Hinglish, optional Groq/Gemini/OpenRouter) -> validated steps -> preview -> your approval -> real engine rebuild -> QC -> audit, with undo/redo.
> EMB: six honest capabilities, safe `.emb` inspection, and a Wilcom handoff bundle. **Native EMB creation/editing remains REQUIRES_WILCOM_CONFIGURATION - no fake EMB is ever produced.**
> Read: `AUDIT_REPORT_NEXT_VERSION.md` (includes a key-rotation notice), `CAPABILITY_MATRIX_v0.7.md`, `AI_PROMPT_BOX.md`, `SECURITY.md`, `EMB_INTEGRATION.md`.
> Quick start: `pip install -e .[dev]` -> `embcopilot ask "outer ring 3mm satin, denim, zigzag underlay" ` -> `embcopilot web`. Cloud AI is optional: copy `.env.example` to `.env`.

---
(v0.6 README follows)

# Embroidery AI Copilot v0.6.0

> **Status (honest):** AI-assisted, object-based digitizing + production QC toolkit. *Production-oriented, QC validated, machine-format validated; requires sew-out validation.*
> Native EMB and Wilcom automation are **REQUIRES_WILCOM_CONFIGURATION** (no fake files). PES/JEF/VP3 are UNAVAILABLE. No physical sew-out simulation.
> Everything was tested on **synthetic** artwork. See `AUDIT_REPORT_v0.6.md`, `CAPABILITY_MATRIX_v0.6.md`, `INSTALL.md`.

## What's new in v0.6
Vector glyph lettering + true circular text, parametric circles/rings, Input C satin v2, negative space + protected travel zones, production-aware trim/jump policy,
context-aware short-stitch cleanup, QC v2 with release gates and export blocking, validated export package, project store with undo/redo, opt-in learning/calibration,
EN/Hinglish multi-step command parsing, honest NativeEMBAdapter, `embcopilot badge | benchmark | capabilities | fonts | command | export-all | text-check`.

---
(v0.5 README follows)

# Embroidery AI Copilot

## Product vision

The long-term goal is an **AI + human collaboration** tool for embroidery
digitizing on Wilcom ES-65 and, eventually, other software:

```
Reference Image → Vision → Object Blueprint → Technical Blueprint →
Human Approval → Wilcom Assistance → QC → Production Validation
```

**This is not a "photo in, embroidery out" black box.** Professional
digitizing means deciding stitch type, direction, density, underlay, pull
compensation, start/end points, sequencing, overlaps, connectors, trims,
minimum feature size, and fabric behaviour — this project represents each
of those as an explicit, inspectable concept (see `domain.py`,
`knowledge.py`, `technical.py`) rather than collapsing digitizing into
"trace an image."

**Automation levels** (only Level 1 and a Level-2 *mock* exist today):

| Level | What it means | Status here |
|---|---|---|
| 1 — Advisory | Analysis, recommendations, explanations; no Wilcom interaction | **Implemented** |
| 2 — Semi-automatic, human-in-the-loop | AI proposes actions, a human approves, an adapter executes them | **Architecture + mock adapter only** — no real Wilcom automation |
| 3 — High automation | Larger portions automated | **Not built.** Not attempted. Architecture leaves room for it later |

## What's implemented vs architecture-only vs not implemented

Read this table before trusting any claim elsewhere in this README:

| Capability | State |
|---|---|
| DST parsing/QC analysis (Phase 1) | **Supported** |
| Rule-based stitch-type/density/underlay/pull-comp/direction recommendations | **Supported** (geometry-only rules, not an LLM) |
| Vision V2: background removal (alpha / solid / gradient), holes, anti-alias handling, width profile, centerline, debug export | **Partial** — real and tested on synthetic images; NOT yet validated on real embroidery artwork (see LIMITATIONS.md) |
| Vision: semantic object recognition ("this is a wing") | **Not implemented.** Every detected region's category is `unknown` until a human (or a future model) assigns one |
| Object Blueprint / Technical Blueprint generation | **Supported** |
| Command parsing (natural language → structured intent) | **Supported**, keyword/regex-based, not an LLM |
| Production profiles (11) that change density, underlay tier, pull comp, min feature size, connector weights | **Partial** — real effect on decisions; numeric values are unvalidated starting points |
| Confidence / risk / approval-state model | **Supported** (thresholds now match spec: 95/85/70, configurable, validated) |
| Connector cost-function engine | **Supported** (a cost model, not a claim of one universal optimum) |
| Sequence optimizer (dependency-respecting) | **Supported** |
| Reference-vs-design comparison | **Partial** — coarse object-count/size comparison only, not shape-matching |
| DST file adapter | **Supported** |
| EMB file adapter | **Partial** — detect + undecoded byte-level metadata only. **No EMB parsing or editing.** |
| PES / JEF adapters | **Planned** — raise `NotImplementedCapability` |
| Mock Wilcom adapter (in-memory simulation) | **Supported** |
| Real Wilcom ES-65 adapter | **Not implemented.** Every method raises `NotImplementedCapability` — nothing is faked |
| Screen automation / mouse-keyboard control | **Not implemented** |
| Session / project file persistence (JSON) | **Supported** |
| Audit log | **Supported**, local-only |
| GUI | **Not built** (architecture described below only) |

No capability in this project claims "fully automatic," "professional
quality guaranteed," or "Wilcom fully automated." Where something isn't
real yet, the code raises `NotImplementedCapability` rather than returning
a plausible-looking fake result.

## Architecture

```
                    REFERENCE IMAGE
                          |
                    VISION ENGINE            vision.py (LocalVisionProvider: real,
                          |                              geometry-only; CloudVisionProvider:
                          v                              architecture-only placeholder)
                  OBJECT BLUEPRINT          vision.ObjectBlueprint (domain.EmbroideryObject
                          |                  list, category="unknown" until assigned)
                          v
              EMBROIDERY KNOWLEDGE ENGINE    knowledge.py (rule-based, not an LLM)
                          |
                          v
                TECHNICAL BLUEPRINT          technical.py (TechnicalBlueprintEngine)
                          |
                          v
                  DECISION + CONFIDENCE      domain.Decision / domain.Confidence
                          |
                          v
                   HUMAN APPROVAL            domain.ApprovalState
                          |
                          v
                    ACTION PLAN              automation/schema.py (ActionRequest)
                          |
                          v
                  WILCOM ADAPTER             automation/wilcom_adapter.py (interface;
                          |                   WilcomES65Adapter raises NotImplementedCapability)
                          v                   automation/mock_adapter.py (MockWilcomAdapter:
              WILCOM ES-65 (not connected)     real in-memory simulation, used by default)
                          |
                          v
                  QC + VALIDATION            analyze.py / rules.py (Phase 1) +
                          |                   comparison.py (coarse reference-vs-design)
                          v
                 FINAL PRODUCTION
```

```
src/embcopilot/
  __init__.py, __main__.py, cli.py     entry points
  models.py, dst.py, analyze.py,        Phase 1: DST parsing + QC analysis
  metrics.py, rules.py, config.py,
  report.py
  domain.py                             Phase 2 core model (format-independent)
  vision.py                             Object Blueprint (real geometry-only detection)
  knowledge.py                          rule-based digitizing knowledge engine
  technical.py                          Technical Blueprint engine + small-detail check
  command.py                            NL -> structured command, anti-hallucination
  connector.py                          connector cost-function engine
  sequence.py                           dependency-respecting sequence optimizer
  comparison.py                         coarse reference-vs-design comparison
  session.py                            CopilotSession + JSON project files
  audit.py                              local, append-only decision/action log
  automation/
    schema.py        ActionRequest/ActionResult/AutomationPolicy
    wilcom_adapter.py  WilcomAdapter interface + honest WilcomES65Adapter placeholder
    mock_adapter.py    MockWilcomAdapter — real in-memory simulation
    executor.py        ActionExecutor: policy checks, logging, emergency stop
    emb_adapter.py      DesignFileAdapter: DST (full), EMB (detect-only), PES/JEF (planned)
tests/            pytest suite (27 files, 368 tests) — includes test_vision_v2.py and test_profiles.py
samples/          hummingbird_sample.dst (synthetic Phase 1 regression fixture)
scripts/          make_sample.py
```

## Phase 1 features (DST QC analyzer)

Unchanged in behaviour from the previous version, with the correctness
fixes below. See the "Phase 1 correctness fixes" section for what changed
and why.

- Stitch/jump/colour-change counts, dimensions, estimated thread/bobbin,
  estimated production time
- QC rules: estimated trims, wasted travel, tiny/short/long stitches,
  estimated local thread-density proxy, fragmented segments, heavy
  (area-normalised) stitch count
- Fabric presets (`woven`, `knit`, `cap`, `towel`, `leather`), every
  threshold individually overridable
- HTML dashboard + JSON export, never modifies the source `.dst`

## Phase 1 correctness fixes (this version)

An audit of the existing project found and fixed:

1. **Jump travel was net displacement, not total distance.** "5mm right,
   5mm left, 5mm right" was reported as 5mm of travel (the net vector);
   it is actually 15mm. Fixed in `analyze._reconstruct_segments`: travel is
   now the sum of each jump's own length, and the same total feeds the
   estimated-trim heuristic.
2. **Segment length silently dropped the first stitch.** A segment's very
   first stitch (the movement from wherever the needle last was — often a
   jump landing point — to the segment's first penetration) was excluded
   from `Segment.length_mm`. Fixed to include it.
3. **Colour-block bookkeeping had two edge cases.** A completely empty
   design, and the *final* colour block when it has zero stitches, could
   both end up with `len(color_blocks) != colors`. Fixed so every colour
   index always gets an entry, even with zero stitches.
4. **Density terminology.** Now consistently labelled "estimated local
   thread-density proxy," never implied to be Wilcom's own density value.
5. **Bounding-box stitch count.** Explicitly labelled an "area-normalized
   stitch metric," never called a density figure — a sparse outline and a
   solid fill of the same stitch count score identically on this metric,
   and the report says so.
6. **Test coverage expanded** for all of the above (see `test_analyzer.py`).

## Phase 2 features

### Vision engine (`vision.py`)

`LocalVisionProvider` is a **real, working, offline** pipeline: colour
quantisation (k-means) → per-colour contour extraction (OpenCV) → noise
filtering → bounding box + simplified outline + area, converted to
millimetres using the image size and your stated target physical size (it
never assumes 1 pixel = 1 mm).

**What it is not**: a semantic object detector. It cannot tell you a
region is a "wing" — every detected region's `category` is `unknown` until
a human or a future model assigns one. Confidence per region is a
geometric proxy (rewards larger, smoother regions) and says so in its own
`reason` field — it is not a trained model's probability.
`CloudVisionProvider` is an architecture-only placeholder that raises
`NotImplementedCapability`; no reference image is ever sent anywhere by
this codebase (see Privacy below).

Example (from `embcopilot blueprint`):
```
2 region(s) detected in bird.png (target size 80.0 x 80.0 mm)
  Region 1   category=unknown  bbox_mm=(12.0,8.0)-(48.0,44.0)  confidence=71%
  Region 2   category=unknown  bbox_mm=(50.0,10.0)-(72.0,40.0) confidence=64%
```

### Knowledge engine (`knowledge.py`)

Rule-based, **not an LLM**: explicit width/aspect-ratio/area thresholds
decide stitch type, and every function returns the recommendation *plus*
its reasoning and a `Confidence`:

- Width < 1mm → Run; 1–10mm & elongated → Satin; 10–12mm → Satin/split
  boundary; > 12mm → Tatami. A round shape inside the satin width band is
  redirected to Tatami (a true satin column needs a defined long axis).
- Density, underlay, and pull compensation follow the same width/fabric
  rules established for this project (0.40mm satin density; underlay
  scaling from center-run to double-zigzag by width; pull compensation
  scaling with width and fabric stretch).
- Direction: a real PCA computation on the region's own outline points
  finds its principal axis; satin gets a perpendicular angle, fills get
  45° by default (never 0/90°, which visibly tracks the fabric grain).

### Technical Blueprint (`technical.py`)

Turns an Object Blueprint into a per-object digitizing plan by calling the
knowledge engine for every object, and flags — never silently deletes —
any feature smaller than a configurable practical floor (default 0.5mm at
target size):

```
Region 2   stitch=satin    density=0.4mm underlay=zigzag
           pull_comp=0.2mm direction=97.3° confidence=87%
  reason: Width is 4.20mm, inside the 1-10mm satin band, with a clearly
          elongated shape (ratio 3.8) — a controlled column fits.
```

### Command system (`command.py`)

Natural language → structured command, **not** a direct LLM-to-Wilcom
pipeline (see Automation Safety):

```
"Punch all leaves."
  -> {"intent": "digitize", "kind": "action", "target_category": "leaf",
      "scope": "category", "constraints": {}}
```

**Never hallucinates existence.** The command is then validated against
what a vision pass actually detected:
```
"Trace only branches." (0 branch-category objects detected)
  -> matched_object_ids: []
  -> note: "I detected 0 confirmed 'branch' objects. Would you like me to
            inspect the reference image again, or assign categories
            manually first?"
```
Question phrasing is recognised even when it contains an action verb:
`"How should I digitize this wing?"` parses as a QUERY, not an ACTION.

### Confidence (`domain.Confidence`, `domain.ConfidenceThresholds`)

Confidence is **object-specific**, never one global number for a whole
design:
```
Region 1 (stitch type): 87%   Region 1 (direction): 91%
Region 2 (stitch type): 55%   Region 2 (direction): 60%
```
Default bands (configurable, not universal truths):
`>=90% automatic-safe · 85-89% ask approval · 70-84% require review · <70% do not auto-execute`

### Safety: approval, risk, and the automation policy

- `ApprovalState`: PROPOSED → APPROVED/REJECTED/MODIFIED → EXECUTING →
  COMPLETED/FAILED. Nothing moves from PROPOSED to executed without this.
- `RiskLevel`: SAFE / LOW / MEDIUM / HIGH / IRREVERSIBLE.
- `AutomationPolicy.may_run_unattended()` decides whether a given
  `ActionRequest` may run without a human — the default policy only allows
  SAFE actions unattended, and nothing here makes it more permissive on
  its own.
- **The LLM never drives the mouse.** The pipeline is natural language →
  `CommandParser` → structured `ActionRequest` → `AutomationPolicy` check →
  `ActionExecutor` → adapter. Raw text never reaches an adapter.
- `ActionStatus` is never faked: PLANNED / EXECUTING / SUCCESS / PARTIAL /
  FAILED / UNKNOWN. An action that could not be verified is `UNKNOWN`, not
  `SUCCESS`.
- `ActionExecutor` stops itself after any failure (`stop()`/`reset()`) —
  it never keeps running a queued plan past a failure, and refuses every
  further call while stopped (`EmergencyStopActive`).

### Connector & sequence engines

`connector.py` prices TRIM / JUMP / HIDDEN_TRAVEL per gap with a
configurable cost function — **minimum trims is not treated as
automatically optimal**; a cheap trim can beat a jump that would cross
open, visible fabric, and the weights are yours to tune per
quality/speed profile.

`sequence.py` topologically sorts by declared dependencies
(`underlay → base_fill → detail → outline` by default) and **raises
`DependencyCycleError`** rather than silently picking an arbitrary order —
it never reorders a dependency after its dependent, even if that would
shorten travel.

### EMB / Wilcom support — read this before assuming anything

**EMB** is Wilcom's proprietary, undocumented native format. This project
does **not** reverse-engineer it, bypass its licensing, or pretend to
parse its internal object model. `EMBAdapter` only does what's legitimate
without any of that: extension detection, and an **undecoded** byte-level
signature + file size, explicitly labelled "EMB native editing capability:
Not available in current build."

**Wilcom automation** has no official API to build on. `WilcomAdapter`
defines the intended capability surface (`select_tool`, `create_geometry`,
`set_properties`, etc.); `WilcomES65Adapter` is a real class you can
instantiate, but **every method raises `NotImplementedCapability`** — none
of it is faked. `MockWilcomAdapter` is a real, working in-memory
simulation of that same surface, used by `embcopilot simulate` and
intended as the development/testing target until (if) a legitimate
integration path (an official API, an accessibility API, or a supported
plugin interface) exists.

### Session & audit

`CopilotSession` ties a design's whole state together and saves as plain
JSON (`.ecp.json`) — **never** a proprietary binary, and never an embedded
copy of a `.emb` file's bytes. A native file is recorded as a path + a
SHA-256 hash (`NativeFileReference`), so a project file can later verify
whether that file changed without ever copying it. `AuditLog` is a local,
append-only JSON-Lines log of every decision and action result — never
uploaded, never containing file contents.

### Privacy

- No reference image or design file is ever uploaded automatically.
  `CloudVisionProvider` is architecture-only and unimplemented for exactly
  this reason.
- Core QC analysis (`analyze`/`qc`) works fully offline and always will —
  see "Offline-first" below.
- Logs stay local (`logs/` by default) and are never sent anywhere by this
  codebase.

## Installation

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .
```

Phase 1 (`analyze`/`qc`) needs nothing beyond that. For Phase 2's vision
features (`blueprint`, `technical-plan`, `simulate` with an image):

```bat
pip install -e .[vision]
```

For the test suite:

```bat
pip install -e .[dev]
pytest
```

## Usage

Phase 1 (unchanged):
```bash
embcopilot design.dst
embcopilot design.dst --fabric cap --spm 900
embcopilot analyze design.dst --json --output reports/
embcopilot qc design.dst          # alias for analyze
embcopilot --list-fabrics
```

Phase 2:
```bash
embcopilot blueprint photo.png --target-width-mm 80 --target-height-mm 80
embcopilot technical-plan photo.png --target-width-mm 80 --target-height-mm 80 --fabric cap
embcopilot simulate photo.png --target-width-mm 80 --target-height-mm 80
embcopilot simulate --dry-run     # print the action plan only, execute nothing
```

`simulate` only ever talks to `MockWilcomAdapter` — no real Wilcom
installation is touched by any command in this project today.

### Roadmap CLI commands (not yet implemented)

`embcopilot validate` and a standalone `embcopilot report` are not built —
`analyze`/`qc` already covers validation + reporting for DST files. They
are left here as a documented gap rather than silently absent.

## Offline-first

`analyze`/`qc` (Phase 1) requires no network access and never will. The
Phase 2 vision engine (`LocalVisionProvider`) is also fully offline (it's
OpenCV + k-means, run locally). Any future cloud AI integration
(`CloudVisionProvider` or similar) will be opt-in and disclosed, never a
silent default.

## Testing

```bash
pip install -e .[dev]
pytest
```

16 test files, 179 tests, covering: DST bit-level round-tripping and
corruption handling; Phase 1 metrics including the jump-travel and
segment-length fixes above; every QC rule's trigger boundary; the domain
model; the vision engine on synthetic generated images (no external test
fixtures needed); the knowledge engine's width/direction rules including
the PCA-based principal-axis computation; the Technical Blueprint engine
including the small-detail flag; the command parser's anti-hallucination
behaviour; the connector cost function's trade-offs; the sequence
optimizer's dependency ordering and cycle detection; the mock Wilcom
adapter and action executor (including emergency stop); every file
adapter's honesty about its own capabilities; session persistence
(including that native file bytes are never embedded); the coarse
comparison engine; and CLI subcommand dispatch including backward
compatibility with the pre-Phase-2 bare-file invocation.

`samples/hummingbird_sample.dst` remains the Phase 1 regression fixture;
tests check logical properties (`stitches > 0`, etc.), never hard-coded
old numbers, so a bug fix is never penalised by its own regression test.

## Limitations (complete list, not just Phase 1's)

- DST is the only fully-supported stitch file format. EMB is detect-only.
  PES/JEF are unimplemented placeholders.
- Vision is geometric, not semantic — it cannot recognise "a wing," only
  "a region of this colour, this shape, this size."
- The knowledge engine's width estimate is a bounding-box proxy
  (`min(bbox_width, bbox_height)`), not a true medial-axis measurement —
  accurate for roughly axis-aligned shapes, an overestimate for diagonal
  or curved ones of the same bounding box.
- The connector/sequence engines operate on abstract gaps and declared
  dependencies; they do not yet consume a real DST/EMB design's actual
  geometry end-to-end (that integration is future work).
- The reference-vs-design comparison is count/size-based only — it cannot
  confirm that a specific reference object corresponds to a specific
  design segment.
- No real Wilcom automation exists. `MockWilcomAdapter` is a simulation;
  `WilcomES65Adapter` raises `NotImplementedCapability` for everything.
- No GUI exists — only the architecture and panel layout are documented.
- Confidence scores throughout are geometric/rule-based proxies, not
  outputs of a trained model — every one of them documents its own basis
  in its `reason` field.
- Nothing in this project has been validated against a real Wilcom ES-65
  installation, a real embroidery machine, or physical stitch-outs.

## Roadmap (future work, not current functionality)

- Real medial-axis width measurement for the knowledge engine
- A genuinely optional, opt-in semantic vision provider
- Wiring the connector/sequence engines into real DST/EMB geometry
- A legitimate Wilcom integration path, if/when one exists (official API,
  accessibility API, or supported plugin/macro interface) — Level 2
  automation only becomes real once this does
- PES/JEF adapters
- Shape-matching (not just count-based) reference-vs-design comparison
- The desktop GUI described in the architecture section above


## v0.4.0 quick start
```
pip install -e .            # needs numpy + opencv-python
embcopilot demo --out-dir demo                       # full pipeline on a bundled synthetic reference
embcopilot stitches art.png --target-width-mm 100 --target-height-mm 100 --profile quality_first --out-dir out
embcopilot simulate art.png --target-width-mm 100 --target-height-mm 100 --dry-run
embcopilot inspect-emb file.emb                      # honest capability report, nothing faked
```
See CAPABILITY_MATRIX.md for what is and is not real.


## v0.5.0 quick start (no terminal after the first launch)
```
pip install -e .                       # numpy + opencv-python
python -m embcopilot web               # or double-click start_copilot.bat / start_copilot.sh -> browser dashboard
python -m embcopilot demo --out-dir demo
python -m embcopilot stitches art.svg --target-width-mm 100 --target-height-mm 100 --profile quality_first --fabric T_SHIRT --out-dir out
python -m embcopilot threads "#1E6ACB" --palette-csv my_madeira.csv --brand Madeira
pytest -q
```
See CAPABILITY_MATRIX.md, FORMAT_SUPPORT.md, EMB_WORKFLOW.md, GUI.md, API.md.
