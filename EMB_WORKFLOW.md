# EMB WORKFLOW
Status: **UNAVAILABLE — `EMB_API_NOT_CONFIGURED`.** Wilcom EMB is proprietary; this project does not reverse-engineer it and no supported Wilcom API/service is configured.
`EMBAdapter` exposes detect/inspect/open/create/edit/save/export. `detect/inspect` work on bytes (metadata only). Every authoring call raises `EmbUnavailable` and writes nothing.
To enable: implement `EMBBackend` (create/open/edit) for a supported route (licensed SDK/service or UI automation of an installed Wilcom), set it on the adapter, and run `verify_round_trip` (create → open → edit → reopen → verify). Only after that passes does `status()` report IMPLEMENTED (tested with a fake backend: PARTIAL before, IMPLEMENTED after).
Hand-off today: export DST, open it in Wilcom, save as EMB yourself; or use the `.ecd.json` project file.
