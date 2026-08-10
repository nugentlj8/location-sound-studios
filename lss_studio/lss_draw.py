"""Drawing primitives on Pillow only - no Cairo, no native libraries.

Everything is drawn at SS times the final size and downsampled with LANCZOS,
which gives clean antialiasing on curves and text without a vector backend.
"""

from PIL import Image, ImageDraw, ImageFont

SS = 2


def rgb(h):
    if not isinstance(h, str):
        return h
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    """Blend colour a toward b by t, as an (r,g,b) tuple.

    Used instead of alpha because everything is drawn on an opaque RGB canvas;
    a flat blend against the known sky is indistinguishable from opacity here
    and costs no compositing pass.
    """
    ca, cb = rgb(a), rgb(b)
    t = max(0.0, min(1.0, t))
    return tuple(int(round(ca[i] + (cb[i] - ca[i]) * t)) for i in range(3))


def level_points(y0, amp, lv, W):
    w = (W + 60) / len(lv)
    return [(-30 + (i + 0.5) * w, y0 - amp * v) for i, v in enumerate(lv)], w


def catmull(P, per=16):
    """Flatten a Catmull-Rom spline through P into a dense polyline."""
    out = []
    for i in range(len(P) - 1):
        p0, p1 = P[max(i - 1, 0)], P[i]
        p2, p3 = P[i + 1], P[min(i + 2, len(P) - 1)]
        for s in range(per):
            t = s / per
            t2, t3 = t * t, t * t * t
            out.append((
                0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3),
                0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)))
    out.append(P[-1])
    return out


def draw_line(dr, y0, amp, lv, W, col, sw, mode="steps"):
    """One envelope line. Steps use rectangles so the corners stay square."""
    c = rgb(col)
    half = sw / 2.0
    if mode == "curves":
        P, _ = level_points(y0, amp, lv, W)
        P = [(-90.0, P[0][1])] + P + [(W + 90.0, P[-1][1])]
        pts = [(x * SS, y * SS) for x, y in catmull(P)]
        dr.line(pts, fill=c, width=max(1, int(round(sw * SS))), joint="curve")
        return
    w = (W + 60) / len(lv)
    x, prev = -30.0, None
    for v in lv:
        y = y0 - amp * v
        dr.rectangle([x * SS, (y - half) * SS, (x + w) * SS, (y + half) * SS], fill=c)
        if prev is not None:
            lo, hi = sorted((prev, y))
            dr.rectangle([(x - half) * SS, (lo - half) * SS,
                          (x + half) * SS, (hi + half) * SS], fill=c)
        prev = y
        x += w


def text_run(dr, s, size, tracking, x, baseline, col, font_path):
    """Per-glyph placement so tracking is exact. Returns the ending x."""
    f = ImageFont.truetype(font_path, max(1, int(round(size * SS))))
    c = rgb(col)
    px = x * SS
    for ch in s:
        if ch != " ":
            dr.text((px, baseline * SS), ch, font=f, fill=c, anchor="ls")
        px += f.getlength(ch) + tracking * SS
    return px / SS


def text_width(s, size, tracking, font_path):
    f = ImageFont.truetype(font_path, max(1, int(round(size * SS))))
    return (sum(f.getlength(c) for c in s) + tracking * SS * (len(s) - 1)) / SS


def new_canvas(W, H, bg="#13232E"):
    img = Image.new("RGB", (W * SS, H * SS), rgb(bg))
    return img, ImageDraw.Draw(img)


def finish(img, W, H, path):
    img.resize((W, H), Image.LANCZOS).save(path)
    return path


def solid_mask(W, H, path):
    Image.new("RGB", (W, H), (255, 255, 255)).save(path)
    return path


def draw_filled(dr, y0, amp, lv, W, baseline, col, mode="steps"):
    """Skyline as a solid silhouette from the roofline down to a baseline."""
    c = rgb(col)
    if mode == "curves":
        P, _ = level_points(y0, amp, lv, W)
        P = [(-90.0, P[0][1])] + P + [(W + 90.0, P[-1][1])]
        top = catmull(P)
    else:
        top, x = [], -30.0
        w = (W + 60) / len(lv)
        for v in lv:
            y = y0 - amp * v
            top += [(x, y), (x + w, y)]
            x += w
    poly = [(p[0] * SS, p[1] * SS) for p in top]
    poly += [(top[-1][0] * SS, baseline * SS), (top[0][0] * SS, baseline * SS)]
    dr.polygon(poly, fill=c)


def row_layout(H, n, filled=False, top=0.392, bot=0.736):
    """(y, amplitude) for n stacked envelope rows inside a fixed vertical zone."""
    if n <= 1:
        # Bottom-anchored: the shortest building sits near the frame bottom and
        # towers rise a fixed height upward, so tall towers clear the slate while
        # the skyline hugs the lower edge. Independent of top/bot.
        base_frac = 0.930                       # shortest tower base, above bottom
        amp = 0.218 if not filled else 0.150    # tower height (level range = 1.4)
        y0 = base_frac - amp * 0.45             # so level -0.45 lands at base_frac
        return [(y0 * H, amp * H)]
    span = (bot - top) / (n - 1)
    mid = (top + bot) / 2.0
    half = (bot - top) / 2.0
    out = []
    for i in range(n):
        y = top + span * i
        k = 1.0 if n <= 2 else 1.0 - 0.6 * abs(y - mid) / half
        amp = min(span * 0.62, 0.16) * k
        out.append((y * H, amp * H))
    return out


def split_at_xs(pts, bounds):
    """Cut a left-to-right polyline at the given x positions."""
    if not bounds:
        return [pts]
    segs, cur, i = [], [pts[0]], 0
    for a, b in zip(pts, pts[1:]):
        while i < len(bounds) and a[0] < bounds[i] <= b[0]:
            x = bounds[i]
            t = 0.0 if b[0] == a[0] else (x - a[0]) / (b[0] - a[0])
            pt = (x, a[1] + (b[1] - a[1]) * t)
            cur.append(pt)
            segs.append(cur)
            cur = [pt]
            i += 1
        cur.append(b)
    segs.append(cur)
    return [s for s in segs if len(s) > 1]


def envelope_pts(y0, amp, lv, W, mode="steps"):
    """The polyline for one envelope row, in final-image coordinates."""
    if mode == "curves":
        P, _ = level_points(y0, amp, lv, W)
        P = [(-90.0, P[0][1])] + P + [(W + 90.0, P[-1][1])]
        return catmull(P)
    pts, x = [], -30.0
    w = (W + 60) / len(lv)
    for v in lv:
        y = y0 - amp * v
        pts += [(x, y), (x + w, y)]
        x += w
    return pts


# ------------------------------------------------------- generative scenes
# Depth is expressed as distance from the SKY, not as absolute darkness. The
# reference art is near-black trees on white; on the night sky near-black trees
# would simply vanish, so mountains are mixed toward the background to recede
# and trees are left at full strength to come forward. That reads correctly on
# a pale sky and inverts sensibly on a dark one.
MTN_RIDGE = 0.35                 # how far the ridgeline sits toward the sky
# The two flat faces, as a distance from the SKY - so the lit face is the one
# mixed LESS far toward it, whichever way round sky and silhouette happen to be.
# Separated enough to read as lit and shadow, but both kept well away from full
# strength: the trees fill solid as the progress indicator, so anything
# approaching their weight up here costs the read on mountains_forest. The gap
# between the two carries the facet; their distance from the sky carries depth.
MTN_LIT, MTN_SHADOW = 0.68, 0.87
MTN_RIDGE_FACE = 0.52            # a fill already defines the ridge - the
                                 # stroke only needs to keep the edge crisp
TREE_FAINT = 0.62                # an unplayed tree, when drawn faint. Further
                                 # toward the sky than this and it holds on the
                                 # night sky but washes out on Canopy, where
                                 # amber into green loses contrast fast
LW_RIDGE, LW_CREASE = 4.5, 3.5   # design units
LW_TREE, LW_HOUSE = 3.5, 4.0


def _lum(c):
    v = [x / 255.0 for x in rgb(c)]
    v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def _depth(t, col, bg):
    """Scale a mix-toward-the-sky by how much contrast there is to spend.

    A bright foreground on a dark sky can be pushed a long way back and still
    read. A mid-toned accent on that same sky goes muddy at the identical
    setting - which left the mountains behind the played half almost black
    while the unplayed half kept its two tones. Measuring the contrast instead
    of hard-coding the amount keeps both halves at a comparable weight, in any
    palette.
    """
    a, b = _lum(col), _lum(bg)
    c = (max(a, b) + 0.05) / (min(a, b) + 0.05)
    return t * max(0.60, min(1.0, (c / 8.0) ** 0.5))


def _pts(poly, k):
    """Design units to supersampled image coordinates."""
    return [(x * k * SS, y * k * SS) for x, y in poly]


def _stroke(dr, poly, k, col, lw, close=False):
    p = _pts(poly, k)
    if close:
        p = p + [p[0]]
    if len(p) < 2:
        return
    dr.line(p, fill=rgb(col), width=max(1, int(round(lw * k * SS))),
            joint="curve")


def _band_col(bands, x, k, fallback):
    """The colour the playback cycle is showing at this x."""
    if not bands:
        return fallback
    px = x * k
    for edge, col in bands:
        if px <= edge:
            return col
    return bands[-1][1]


def draw_scene(dr, sc, W, H, col, bg, played=False, face="outline",
               ahead="outline", filled=False, bands=None):
    """One generative silhouette, back to front.

    `col` is the state colour: the foreground ahead of the playhead, the accent
    behind it. The two layers are otherwise identical, so the sliding mask in
    the video turns one into the other exactly where the playhead is.

    Shapes are filled with the SKY before they are stroked. That is what gives
    a nearer shape occlusion over a further one without a vector clipper, and
    it is why an outline-only mountain still hides the range behind it.
    """
    k = W / 1280.0

    for m in sc.get("mountains", []):
        c = _band_col(bands, m["cx"], k, col)
        if face == "twotone":
            # both faces stay washed toward the sky: the trees fill solid as
            # the progress indicator, and if the mountains fill at anything
            # like full strength mountains_forest turns to mud
            dr.polygon(_pts(m["shadow"], k), fill=mix(c, bg, _depth(MTN_SHADOW, c, bg)))
            dr.polygon(_pts(m["lit"], k), fill=mix(c, bg, _depth(MTN_LIT, c, bg)))
        else:
            dr.polygon(_pts(m["poly"], k), fill=rgb(bg))
        ridge = mix(c, bg, _depth(MTN_RIDGE_FACE if face == "twotone"
                                  else MTN_RIDGE, c, bg))
        _stroke(dr, m["poly"][:-1], k, ridge, LW_RIDGE)   # flanks, not the base
        _stroke(dr, m["crease"] if face == "twotone" else m["spur"],
                k, ridge, LW_CREASE)

    for t in sc.get("trees", []):
        c = _band_col(bands, t["cx"], k, col)
        if played:
            dr.polygon(_pts(t["poly"], k), fill=rgb(c))
        elif ahead == "faint":
            dr.polygon(_pts(t["poly"], k), fill=mix(c, bg, _depth(TREE_FAINT, c, bg)))
        else:
            dr.polygon(_pts(t["poly"], k), fill=rgb(bg))
            _stroke(dr, t["poly"], k, c, LW_TREE, close=True)

    # houses, then the street trees in front of them. Both take the same fill
    # treatment: in a town the whole row is what the playhead recolours, so a
    # tree filling on the forest's schedule would fight that read.
    town = []
    for h in sc.get("houses", []):
        # chimney first, house over it: the stack runs down to the ground so it
        # is never left hanging, and the body then hides everything below the
        # roof it pokes through
        town += [(p, h["cx"]) for p in
                 ([h["chimney"]] if h.get("chimney") else []) + [h["poly"]]]
    town += [(t["poly"], t["cx"]) for t in sc.get("town_trees", [])]
    for poly, cx in town:
        c = _band_col(bands, cx, k, col)
        if filled:
            dr.polygon(_pts(poly, k), fill=rgb(c))
        else:
            dr.polygon(_pts(poly, k), fill=rgb(bg))
            _stroke(dr, poly, k, c, LW_HOUSE, close=True)


def progress_composite(bone, clay, frac, out):
    """Bone left of the playhead replaced by clay, exactly as ffmpeg's sliding
    mask does it. Lets a still show a mid-playback frame without an encode."""
    b = Image.open(bone).convert("RGB")
    c = Image.open(clay).convert("RGB")
    x = max(0, min(b.width, int(round(b.width * frac))))
    if x:
        b.paste(c.crop((0, 0, x, b.height)), (0, 0))
    b.save(out)
    return out


def draw_banded(dr, y0, amp, lv, W, sw, mode, bands, filled=False, baseline=None):
    """Draw one row where the colour changes at fixed x positions.
    `bands` is a list of (x_end, colour); the last x_end should exceed W."""
    pts = envelope_pts(y0, amp, lv, W, mode)
    segs = split_at_xs(pts, [b[0] for b in bands[:-1]])
    for seg, (_, col) in zip(segs, bands):
        c = rgb(col)
        if filled:
            poly = [(p[0] * SS, p[1] * SS) for p in seg]
            poly += [(seg[-1][0] * SS, baseline * SS), (seg[0][0] * SS, baseline * SS)]
            dr.polygon(poly, fill=c)
        else:
            p = [(x * SS, y * SS) for x, y in seg]
            w = max(1, int(round(sw * SS)))
            dr.line(p, fill=c, width=w, joint="curve" if mode == "curves" else None)
            r = w / 2.0
            for x, y in p[1:-1]:
                if mode == "curves":
                    dr.ellipse([x - r, y - r, x + r, y + r], fill=c)
                else:
                    dr.rectangle([x - r, y - r, x + r, y + r], fill=c)
