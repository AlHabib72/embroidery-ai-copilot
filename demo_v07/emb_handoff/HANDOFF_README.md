# Wilcom handoff bundle

**This is NOT an EMB file.** Native .EMB can only be produced by Wilcom (or a supported Wilcom integration).

- `design.dst` - machine stitches, QC-gated and read-back verified: EXPORTED_VERIFIED. Stitch-level only.
- `design_objects.svg` - one path per object (holes preserved, millimetres). Use it as artwork to rebuild editable objects.
- `object_manifest.csv/.json` - per object: sequence, stitch type, width, density, underlay, pull compensation, angle, thread colour, entry/exit.

## Suggested path to a real EMB
1. In Wilcom open the DST for a stitch-level reference, or bring in `design_objects.svg` as artwork.
2. Re-create/convert objects following `object_manifest.csv` (types, sequence, parameters).
3. Save as EMB inside Wilcom.

Values are heuristic engine outputs; validate with a sew-out.
