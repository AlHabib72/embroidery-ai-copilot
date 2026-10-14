# Native EMB (v0.6) - REQUIRES_WILCOM_CONFIGURATION
`.emb` is Wilcom's proprietary container. This project never writes it itself, never renames DST/EXP to EMB and never reverse-engineers it.
`NativeEMBAdapter` (nativeemb.py) exposes: detect_capability, inspect, create, add_object, modify_object, delete_object, resequence, save, export, validate, edit_test.
Backends: `UnavailableBackend` (default), `WilcomWebAPIBackend` (`EMBCOPILOT_WILCOM_API_URL`+`_KEY`), `WilcomEWABackend` (`EMBCOPILOT_WILCOM_SDK`),
`WilcomDesktopBridge` (`EMBCOPILOT_WILCOM_EXE`, Windows). The three Wilcom backends are **stubs**: when configured they report `CONFIGURED_PROTOCOL_NOT_IMPLEMENTED`
because no verified client exists in this build. To make EMB real, subclass `EMBBackendV2` against your licensed Wilcom interface.
Verification contract (`adapter.export`): create → save → file exists → reopen through the native route → object count → dimensions → only then `design.emb` is kept
and the button becomes "Export EMB". Any failure ⇒ `EMB_EXPORT_FAILED`, file deleted, `download:false`.
UI: "Configure EMB Export" / "EMB Unavailable" / "Export EMB". Message: *Native EMB Export Unavailable - Wilcom native export backend is not configured. Available: DST, EXP.*
The tests use a test double that implements the contract; it does NOT write Wilcom files.
