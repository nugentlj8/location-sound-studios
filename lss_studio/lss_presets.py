"""Preset loading and colour resolution, shared by lss_render and lss_studio."""

import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "lss_presets.json")
INK = (0x13, 0x23, 0x2E)

FALLBACK = {
    "series": {"Sounds of the City": {"name": "SOUNDS OF THE CITY", "accent": "#CF7A34"}},
    "themes": {"None (use series colour)": {}},
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
        return d
    except Exception:
        return FALLBACK


def series_names(p):
    return list(p["series"].keys())


def theme_names(p):
    return list(p["themes"].keys())


def valid_hex(s):
    return bool(HEX.match(s.strip())) if s else False


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast_on_ink(h):
    """WCAG contrast ratio of a colour against the ink background."""
    a, b = _lum(h), _lum("#13232E")
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


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
