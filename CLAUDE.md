# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows desktop app (Tkinter GUI + CLI) that turns a field-recording audio file into a YouTube
thumbnail and a full-length video. The video's skyline silhouette is drawn from the recording's own
loudness envelope — quiet sections make short buildings, loud moments make tall towers — with a live
clock overlay synced to a configured start time. Used to publish a series of ambient city/nature
soundscape recordings ("Sounds of the City" etc.) with consistent branding.

## Running it

```
Setup.bat                          # one-time: installs ffmpeg (winget) + numpy, pillow via pip
Location Sound Studios.bat         # launch the GUI (console window, shows errors on crash)
Location Sound Studios.vbs         # launch the GUI via pythonw, no console window
```

CLI usage (bypasses the GUI, same render engine):
```
py lss_studio/lss_render.py <audio> --place "..." --city "..." --conditions "..." --date YYYY-MM-DD --start "06:30 PM"
py lss_studio/lss_render.py --list-presets      # show available series/theme names
```
See `lss_render.py main()` for the complete flag list — it's the authoritative source.

There is no automated test suite. Verify changes by actually rendering: run the CLI against a short
audio clip (or `--thumb-only` to skip the slow video encode) and inspect the output PNG/MP4.

## Architecture

The three modules worth knowing before you edit them (`lss_presets.py` and `lss_draw.py` are
self-explanatory on reading):

- **`lss_render.py`** — the render engine and CLI entry point (`main()`). This is where audio
  becomes pixels:
  1. `envelope()` streams the audio through `ffmpeg` to raw PCM and computes windowed RMS in dBFS
     without loading the whole file into memory (so multi-hour recordings are fine).
  2. `to_levels()` reduces that envelope to N "buildings" and maps them into a drawable range, under
     one of three `--scale` modes (rank-based "Skyline", percentile-based "Auto", or fixed dB
     "Fixed loudness").
  3. `find_standouts()` / `block_edges()` detect unusually loud moments (median/MAD outlier
     detection) and snap nearby building edges onto them, so a siren or shout visibly lines up with a
     tall tower instead of being averaged into a neighboring block.
  4. `compose()` is the single frame-composition function shared by the thumbnail and every video
     frame — the video is literally the thumbnail layout rendered at a larger size, with the
     timestamp slot left blank for ffmpeg's `drawtext` to fill in live.
  4b. For a silhouette style, `compose()` hands off to `lss_draw.draw_scene()` instead, drawing the
     geometry `lss_scene.build()` produced. The `played` flag picks which side of the playhead the
     frame represents; `--progress` composites the two into one still without an encode.
  5. `video_layers()` + `build_video()` build the video as an ffmpeg `filter_complex` graph: a base
     "bone"-coloured full frame, an accent-coloured ("clay") full frame, and a mask that slides left
     to right over the video's duration so the accent colour appears to "play across" the skyline in
     sync with elapsed time — this is what makes the timeline visually scrub as the clock advances.
  6. `run()` is the orchestration entry point both the GUI and CLI call — writes the thumbnail, the
     video (unless `--thumb-only`), and a `<slug>_render.json` sidecar capturing every parameter used,
     so a past render can be understood or reproduced later. Output goes to a fresh
     `<outdir>/<slug>/` folder per render; it never overwrites an earlier one (auto-numbers instead).
- **`lss_scene.py`** — the generative silhouettes (`mountains`, `forest`, `mountains_forest`,
  `houses`), plus `map_db()`, the single loudness→height mapping that `to_levels()` also calls.
  The rule that makes the rest work: geometry is built **once per render, in 1280x720 design
  units**, and scaled by `k` at draw time. The thumbnail, both video layers and every resolution
  are therefore one shape at different sizes, never separate derivations — which is what makes
  `--detail` count *features* rather than pixels. All randomness comes from a seed hashed off the
  envelope, so a recording always renders identically. `SCENE_STYLES` is the scene→style map and
  `check()` is the up-front validator both the CLI and GUI call; a bad combination is an error,
  never a silent fallback.
- **`lss_studio.py`** — the Tkinter GUI. Builds the config dict expected by `lss_render.run()` and
  calls it in a background thread, polling a `queue.Queue` on a Tk `after()` timer for log lines and
  progress. Not the place to add render logic — it's a thin form over `lss_render.run()`.

Supporting pieces:
- **`lss_presets.json`** — user-editable data, not code. Defines named "series" (each with a name,
  accent colour(s), and geometry) and "themes" (seasonal overlays: alternate colours, a name suffix,
  and optionally a colour `cycle` for the playback line). The updater (`lss_update.py`) explicitly
  never overwrites this file, so users can safely add their own presets.
- **`lss_update.py`** — self-update mechanism. On launch, the GUI checks the `VERSION` file at
  `raw.githubusercontent.com/<user>/<repo>/main/lss_studio/VERSION`; if newer, downloads the program
  files (everything in `FILES` — explicitly *not* `lss_presets.json`) to a temp dir, `py_compile`
  -verifies them, backs up the current set into `lss_studio/previous/`, then swaps them in and
  restarts. This is the only place `GITHUB_USER`/`GITHUB_REPO` are configured — see `RELEASING.md`
  before it's pointed at a real repo.

## Releasing

See `RELEASING.md` for the full process. The essential rule: **bump both `VERSION` files**
(repo root and `lss_studio/VERSION` — the app reads the latter) using plain `MAJOR.MINOR.PATCH`
numbers, then commit and push to `main`. There is no build/package step — pushing *is* the release,
and every running copy of the app picks it up on its next launch via `lss_update.py`.

## Conventions specific to this codebase

- Colours are `#RRGGBB` hex strings passed around as-is; `lss_draw.rgb()` converts to an `(r,g,b)`
  tuple only at the point of drawing.
- Design coordinates are fixed at a 1280x720 basis and scaled by `k = W / 1280.0` everywhere in
  `compose()` — when adjusting layout, change the design-unit constant, not per-resolution numbers.
- `usable()` in `lss_render.py` degrades a configured network drive path (`Z:\...`) to a folder in
  the user's home directory when that drive isn't mounted — this is how the tool stays usable off
  the studio's network.
- Fonts: resolved once at `run()` start via `find_font()`, which checks `LSS_FONT` env var first,
  then falls back through a short hardcoded list (Barlow Condensed Bold preferred, Arial Bold
  fallback on Windows).
