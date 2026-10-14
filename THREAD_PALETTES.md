# THREAD PALETTES
No proprietary shade card is shipped or scraped. Built in: **Generic-24** (project-defined colours G01–G24, *not* any brand).
Import licensed data: CSV with `code,name,hex[,material]` → `ThreadDatabase.load_csv(path, "Madeira")` or `embcopilot threads "#1E6ACB" --palette-csv my.csv --brand Madeira`.
Matching: sRGB → CIE Lab (D65) → CIEDE2000 (validated against Sharma/Wu/Dalal published pairs). ΔE is screen-referred and does **not** predict sewn appearance; the tool never says "exact".
Modes: `nearest`, `min_threads` (reuses a thread when within ΔE 6 of the best, error reported), `manual`. Colorways are independent of geometry (tested: identical stitches for different colorways).

## v0.6
THREAD_COLOR_UNMAPPED is a QC warning. Madeira/Isacord/Gunold remain REQUIRES_LICENSED_RESOURCE: import your own CSV; codes are never fabricated.
