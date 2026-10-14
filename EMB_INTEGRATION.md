# EMB integration (v0.7)
See EMB.md/WILCOM.md for the adapter + state machine. v0.7 adds `emb_studio.py`:
* `emb_capabilities()` - six independent states: EMB_IMPORT, EMB_INSPECTION, EMB_EDIT, EMB_CREATE, EMB_SAVE, EMB_EXPORT (+ handoff_bundle).
* `inspect_emb_bytes()` / `POST /api/emb/inspect` / `embcopilot emb-info file.emb` - file-level facts only; flags renamed DST/PES; never claims object data or native confirmation.
* `handoff_bundle()` / `POST /api/jobs/{id}/emb/handoff` / `embcopilot emb-handoff` - verified DST + design_objects.svg (holes preserved) + object_manifest.csv/json + README. NOT an EMB.
Real EMB needs a supported Wilcom route: subclass `EMBBackendV2` (nativeemb.py); `adapter.export()` only keeps a .emb after create -> save -> reopen -> object count -> dimensions all pass.
