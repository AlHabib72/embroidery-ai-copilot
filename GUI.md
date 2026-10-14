# GUI
`python -m embcopilot web` (or double-click `start_copilot.bat` / `start_copilot.sh`) → http://127.0.0.1:8765. Drag & drop PNG/JPG/WEBP/SVG/AI; machine files are accepted for inspection only.
Flow: Upload → Analyze → review tabs (Reference, Vision, Objects, Blueprint, Technical, Stitches, Preview, QC, Threads, Export, Automation, Audit) → Auto Repair (proposals with measured effect) → Approve → Download. Export buttons come from server capability flags and are disabled with a reason for PES/JEF/EMB and before approval; any repair resets approval.
Architecture: static SPA + `JobManager`; a React/Next or FastAPI layer can reuse `JobManager` unchanged. Not yet viewed in a real browser by the author (JS syntax and every API path are tested).

## v0.6 panel
Command box (English/Hinglish) → validated plan with Approve/Reject (parse only: applying steps is PLANNED), "Run production pass + QC v2", EMB button whose label/enabled state comes from the adapter.
Not built: canvas editor, object inspector editing, drag-move, thread-texture render, React frontend.
