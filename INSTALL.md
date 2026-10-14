# Install
```
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .[dev]          # opencv, numpy, Pillow, fonttools, pytest
embcopilot --version && embcopilot capabilities
embcopilot badge --out-dir out_badge       # parametric vector badge → QC → validated DST/EXP package
embcopilot export-all golden/11_complex_badge.png --out-dir out_raster
embcopilot web                              # browser dashboard on 127.0.0.1:8765
```
Fonts: system fonts are found automatically; add folders with `EMBCOPILOT_FONT_DIRS=/path/a:/path/b` (`;` on Windows). Threads: import your licensed CSV (THREAD_PALETTES.md).
