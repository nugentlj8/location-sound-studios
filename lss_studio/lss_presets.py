"""Preset loading and colour resolution, shared by lss_render and lss_studio."""

import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "lss_presets.json")
INK = (0x13, 0x23, 0x2E)

DEFAULT_BG = "#13232E"           # the sky, when no preset says otherwise
DEFAULT_FG = "#F0E7D6"           # the silhouette and the large slate text

FALLBACK = {
    "series": {"Sounds of the City": {"name": "SOUNDS OF THE CITY", "accent": "#CF7A34"}},
    "themes": {"None (use series colour)": {}},
}

# ---------------------------------------------------------------------------
# Colour presets. THIS is the one structure to edit to add a look - nothing in
# the render code needs touching. Each entry sets the sky (background), the
# silhouette and slate text (foreground), and the accent the playhead reveals.
#
# They are deliberately scene-agnostic: identity comes from the silhouette
# SHAPE, so the same preset has to work for city, town, nature and spaces.
#
# Each was chosen by eye against rendered frames, with WCAG contrast measured
# alongside. Ratios below are fg/bg, accent/bg, accent/fg.
#
# Note the accent/bg figures are low by WCAG's yardstick. That metric is built
# for body text on a background; these accents are wide skyline strokes
# separated from the sky by HUE as much as luminance, and they read clearly at
# thumbnail size. The GUI's low-contrast warning is therefore skipped for these
# vetted presets and kept for hand-typed colours - see lss_studio.start_render.
# ---------------------------------------------------------------------------
COLOR_PRESETS = {
    "None (series colour)": {},
    "Morning": {                                     # 2.40, 1.90, 4.56
        "background": "#D69E10", "foreground": "#FFFFFF",
        "accent": "#BC5A10", "accent2": "#9C4712",
        "note": "low warm sun on deep gold, white skyline",
    },
    "Night": {                                       # 13.08, 4.96, 2.64
        "background": "#13232E", "foreground": "#F0E7D6",
        "accent": "#CF7A34", "accent2": "#F5C98A",
        "note": "the original look - blue hour, sodium-lamp accent",
    },
    "Evening": {                                     # 4.29, 1.80, 7.72
        "background": "#CC5522", "foreground": "#FFFFFF",
        "accent": "#8C3355", "accent2": "#73203F",
        "note": "burnt sunset orange, white skyline, plum playhead",
    },
    "Canopy": {                                      # 3.83, 1.48, 5.66
        "background": "#357A2B", "foreground": "#F2D9A0",
        "accent": "#72491E", "accent2": "#5A3716",
        "note": "sunlit foliage, amber skyline lit against it",
    },
}

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def load():
    try:
        with open(PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        d.pop("_comment", None)
        if not d.get("series"):
            raise ValueError("no series defined")
        d.setdefault("themes", {"None (use series colour)": {}})
    except Exception:
        d = dict(FALLBACK)
    # Colour presets ship in code so the updater actually delivers new ones -
    # lss_presets.json is the user's own file and is never overwritten. A
    # "colors" key there still adds to, or overrides, what ships here.
    colors = dict(COLOR_PRESETS)
    colors.update(d.get("colors") or {})
    d["colors"] = colors
    return d


def series_names(p):
    return list(p["series"].keys())


def theme_names(p):
    return list(p["themes"].keys())


def color_preset_names(p):
    return list(p.get("colors", COLOR_PRESETS).keys())


def valid_hex(s):
    return bool(HEX.match(s.strip())) if s else False


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a, b):
    """WCAG contrast ratio between two colours."""
    x, y = _lum(a), _lum(b)
    hi, lo = max(x, y), min(x, y)
    return (hi + 0.05) / (lo + 0.05)


def contrast_on(h, bg=DEFAULT_BG):
    """WCAG contrast ratio of a colour against a given background."""
    return contrast(h, bg)


def contrast_on_ink(h):
    """Contrast against the default ink sky. Kept for callers that predate
    custom backgrounds."""
    return contrast(h, DEFAULT_BG)


def auto_foreground(bg):
    """A silhouette/text colour that is actually legible on `bg`.

    Only used when nothing has specified one. Picking whichever of bone or ink
    reads better means a bare custom background can never render invisible
    text; on the default sky it returns bone, i.e. the original behaviour."""
    return DEFAULT_FG if contrast(bg, DEFAULT_FG) >= contrast(bg, DEFAULT_BG) else DEFAULT_BG


def series_geometry(series_key, presets):
    s = presets["series"].get(series_key) or list(presets["series"].values())[0]
    return s.get("geometry", "steps")


def theme_extras(theme_key, presets):
    """(suffix, cycle_colours, cycle_minutes) for a theme."""
    t = presets["themes"].get(theme_key) or {}
    return (t.get("suffix", ""), list(t.get("cycle", [])),
            float(t.get("cycle_minutes", 3)))


def resolve(series_key, theme_key, presets, custom=None):
    """Return (series_name, accent, accent2). custom overrides everything."""
    s = presets["series"].get(series_key) or list(presets["series"].values())[0]
    name, accent, accent2 = s["name"], s["accent"], s.get("accent2", "")
    t = presets["themes"].get(theme_key) or {}
    if t.get("accent"):
        accent = t["accent"]
        accent2 = t.get("accent2", "")
    suffix = t.get("suffix", "")
    if suffix:
        name = f"{name} {suffix}"
    if custom:
        if valid_hex(custom.get("accent", "")):
            accent = custom["accent"].strip()
        if custom.get("accent2", "").strip():
            accent2 = custom["accent2"].strip() if valid_hex(custom["accent2"]) else accent2
    return name, accent, accent2


def color_preset(preset_key, presets):
    return (presets.get("colors") or COLOR_PRESETS).get(preset_key) or {}


def resolve_colors(preset_key, theme_key, presets, accent, accent2="", custom=None):
    """Return (background, foreground, accent, accent2).

    A colour preset owns the sky: it always supplies background and foreground.
    It only supplies the accent when no seasonal theme is doing so, since a
    theme's accent and colour cycle are an explicit choice worth keeping - so
    'Night + 4th of July' is the holiday cycle over the night sky. Explicit
    custom colours beat everything.

    `accent`/`accent2` come in already resolved by resolve(), so a theme has
    had its say by the time we get here; theme_sets_accent says whether it did.
    """
    p = color_preset(preset_key, presets)
    theme_sets_accent = bool((presets["themes"].get(theme_key) or {}).get("accent"))

    background = p.get("background") or DEFAULT_BG
    foreground = p.get("foreground") or ""
    if p.get("accent") and not theme_sets_accent:
        accent = p["accent"]
        accent2 = p.get("accent2", "")

    custom = custom or {}
    if valid_hex(custom.get("background", "")):
        background = custom["background"].strip()
    if valid_hex(custom.get("foreground", "")):
        foreground = custom["foreground"].strip()
    if valid_hex(custom.get("accent", "")):
        accent = custom["accent"].strip()
    if valid_hex(custom.get("accent2", "")):
        accent2 = custom["accent2"].strip()

    return background, foreground or auto_foreground(background), accent, accent2
