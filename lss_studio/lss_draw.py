"""Drawing primitives on Pillow only - no Cairo, no native libraries.

Everything is drawn at SS times the final size and downsampled with LANCZOS,
which gives clean antialiasing on curves and text without a vector backend.
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter

SS = 2


def rgb(h):
    if not isinstance(h, str):
        return h
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


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


def add_glow(img, rows, lv, W, col, sw, mode, radius, opacity=0.5):
    """Blur a copy of the lines and screen it back over the image."""
    import numpy as np
    layer = Image.new("RGB", img.size, (0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for y0, amp in rows:
        draw_line(ld, y0, amp, lv, W, col, sw * 1.9, mode)
    layer = layer.filter(ImageFilter.GaussianBlur(radius * SS))
    a = np.asarray(img).astype("float32")
    b = np.asarray(layer).astype("float32") * opacity
    out = 255.0 - (255.0 - a) * (255.0 - b) / 255.0
    return Image.fromarray(out.clip(0, 255).astype("uint8"))


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


def ramp_mask(W, H, path, band_frac=0.32, gamma=1.6):
    """Black, ramping to white over the last band before the right edge."""
    import numpy as np
    band = max(1, int(W * band_frac))
    row = np.zeros(W, dtype=np.float32)
    row[W - band:] = (np.linspace(0, 1, band, dtype=np.float32) ** gamma) * 255.0
    arr = np.repeat(row[None, :], H, axis=0).astype("uint8")
    Image.fromarray(np.dstack([arr] * 3)).save(path)
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
