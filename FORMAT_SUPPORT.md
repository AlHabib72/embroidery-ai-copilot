# FORMAT SUPPORT
| Format | Import | Export | Status | Verification |
|---|---|---|---|---|
| PNG/JPG/WEBP | yes | – | IMPLEMENTED | decode check |
| SVG | yes | – | IMPLEMENTED | well-formed XML |
| AI (PDF-compatible) | yes | – | PARTIAL | `%PDF` + path operators, else `AI_FILE_UNSUPPORTED_VARIANT` |
| DST | read (QC) | yes | IMPLEMENTED | export → read back → compare stitches, colour changes, coordinates (shape, origin-independent) |
| EXP | read (own) | yes | EXPERIMENTAL | same, **own reader only** |
| PES | detect only | no | UNAVAILABLE | needs Brother PEC colour table + thumbnail block |
| JEF | detect only | no | UNAVAILABLE | needs Janome colour table |
| EMB | detect/metadata only | no | UNAVAILABLE | `EMB_API_NOT_CONFIGURED` |
| .ecd.json | yes | yes | IMPLEMENTED | open project format (objects + stitches + colorways), not EMB |
Notes: DST has no trim record: TRIM = 3 zero-length jumps; STOP = colour-change code (machine-specific — verify on your machine). DST/EXP store no thread colours; colours live in the colorway/.ecd.json.

## v0.6 export package
`export_package` writes design.svg/preview.png (technical render from the SAME StitchPlan), qc.json, metadata.json, and machine files only if QC allows and read-back validation passes.
Statuses: EXPORTED_VERIFIED · EXPORT_FAILED_VALIDATION · BLOCKED_BY_QC · UNAVAILABLE (PES/JEF) · REQUIRES_WILCOM_CONFIGURATION (EMB). Missing from the brief's package: object_blueprint.json, qc.html (use v0.5 reports).
