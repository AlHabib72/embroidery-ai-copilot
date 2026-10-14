# Format capabilities (tested in this build)
| Format | Import | Export | Editable objects | Machine stitches | Notes |
|---|---|---|---|---|---|
| DST | yes | yes (read-back verified) | no | yes | v0.5 parser/writer |
| EXP | yes | yes (EXPERIMENTAL) | no | yes | own reader only |
| PES / JEF / VP3 | no | no | - | - | UNAVAILABLE: vendor colour tables needed; no guessed files |
| EMB | inspect only | no (REQUIRES_WILCOM_CONFIGURATION) | native only | via Wilcom | never faked |
| SVG | yes | debug render + per-object artwork | vector | no | not a machine format |
