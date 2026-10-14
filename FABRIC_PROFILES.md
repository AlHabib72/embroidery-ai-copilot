# FABRIC PROFILES
T_SHIRT, JERSEY, DENIM, TOWEL, PIQUE, TWILL, CANVAS, FLEECE, CAP, PATCH, STRETCH_FABRIC — each sets stretch, pile, density scale, underlay bias, minimum feature, maximum satin width, stabilisation hint, risk tolerance, warnings.
Pull compensation = base × fabric(1+1.2·stretch+0.4·pile) × density((0.4/d)^½, .8–1.3) × underlay(0.85 for zigzag) × size(1+min(.3, stitches/20000)) × profile; fills = ½; runs = 0; applied perpendicular to stitch direction.
**All numbers are engineering starting points, not fitted to sew-outs**; confidence is capped below 0.9 and the reason text says "Heuristic model". Use `SewOutRecord` to collect real data.
Hoops: generic-100x100, 130x180, 200x300, cap-60x130 → `VALID` or `DESIGN_EXCEEDS_HOOP`.
