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

`tools/` holds the development scripts (not shipped by the updater, which only sends `lss_studio/`).
When a change is meant to leave existing styles untouched, prove it rather than assert it:
`py tools/identity_check.py baseline` on the old commit, `after` on the new one, then `compare` —
294 cases: 180 style/palette/star/playback-state thumbnails, 7 generated videos, 10 photo-mode
keys, 6 clip-mode ones and 10 looped ones, then (added ahead of the vertical frame) 56 weather
thumbnails, 7 generated covers with their thumbnails, 7 bare slates and 2 weather videos (the rain
one held still with `--no-shimmer`), and 2 shimmering-rain videos — every
video hashed on its DECODED frames rather than the container. The vertical frame itself is not in
it: it is new output, not a style that must stay put.
See `tools/README.md`.

## Architecture

The four modules worth knowing before you edit them (`lss_presets.py` and `lss_draw.py` are
self-explanatory on reading, bar the alpha rule under Conventions):

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
  4a. The slate itself is `lss_draw.draw_slate()` — lifted out of `compose()` unchanged so it needs
     only a draw TARGET, which is what lets photo mode composite the identical slate onto a
     photograph with no second implementation. `slate_boxes()` still measures the same runs at k=1
     and has to move with it.
  4b. For a silhouette style, `compose()` hands off to `lss_draw.draw_scene()` instead, drawing the
     geometry `lss_scene.build()` produced. The `played` flag picks which side of the playhead the
     frame represents; `--progress` composites the two into one still without an encode.
  5. `video_layers()` + `build_video()` build the video as an ffmpeg `filter_complex` graph: a base
     "bone"-coloured full frame, an accent-coloured ("clay") full frame, and a mask that slides left
     to right over the video's duration so the accent colour appears to "play across" the skyline in
     sync with elapsed time — this is what makes the timeline visually scrub as the clock advances.
  5c. `--stars` puts a `_sky` in cfg (from `lss_scene.sky()`) which `compose()` draws *first*, so
     the silhouette occludes it for free. Stars are the same colour in both playback layers, which
     is what lets `star_layers()` twinkle one with a single `drawbox` where a beacon needs two.
     A twinkler only gets filters where `_visible_stars()` finds bare sky under it, read off a
     third "probe" frame composed without the sky — an occluded star would otherwise flash a
     square on top of a building.
  5d. `--cover` adds a 3000x3000 square PNG for Spotify. `_cover()` rebuilds
     the geometry at `dh=1280` off the *same* envelope seed rather than
     cropping or stretching the 16:9 frame, so it is the recording's own
     skyline on a lower ground line with more sky over it. Always the
     fully-played frame.
  5e. `--weather` adds clouds (and, at `rain`, static streaks in front of the
     silhouette) from `lss_scene.weather()`. `compose()` draws them in two calls
     that bracket `draw_scene()` — clouds after the stars so an opaque cloud
     occludes them for free, rain after the silhouette and before the slate, so
     the text reads as floating in front of the weather. Nothing is hand-tuned
     per style or per palette: `lss_draw.weather_tones()` derives every colour
     from the palette's own sky and foreground at a target *contrast ratio*, and
     the cloud band comes from `lss_scene.horizon()` reading the built geometry.
     Rain is deliberately kept out of the twinkle probe (`_clouds_only()`) — a
     cloud occludes a star, a streak does not, and `STAR_CLEAR = 0` would lose a
     twinkler to a single clipped corner.
     In the video the rain SHIMMERS (`rain_layers()`, off with `--no-shimmer`):
     the stars' bargain, not motion. Both layers bake the rain as the still
     does, and a sky-coloured `drawbox` erases a streak for `RAIN_SHIMMER_OFF`
     of its cycle — only where `_visible_rain()` finds its whole rectangle bare
     sky in a third probe, `_rainprobe.png`, which keeps the stars and LIT
     beacons so a box can land on neither. About half the rain qualifies, and
     on/off is all it can do: a leaning streak's rectangle is 7-9x its ink, so
     a partial tone would paint a grey box. The filters go into the chain
     AHEAD of the live clock, so the clock needs no exclusion; the graph string
     is unchanged whenever there are none. The timing is its own RNG stream
     (`RAIN_SHIMMER_SALT`), drawn after clouds and streaks, so nothing already
     in the weather moves. Measured +0-2% encode time, +2-5% file; falling rain
     was 1.2x the runtime and was reverted — see `tools/rain_shimmer.py`.
  5b. A `variants` list in cfg (from `lss_presets.variants()`, thumbnail-only) makes `run()` emit
     one thumbnail per palette instead of one. It sits *after* the envelope, levels and geometry,
     so N looks cost one audio pass and one `compose()` each — and share a silhouette exactly.
     Filenames carry the palette; a folder of variants is otherwise unreviewable.
  5f. Photo mode branches at `_run_photo()`, before the audio pass — it calls `probe_duration()`
     rather than `envelope()`, since there is no geometry to derive. `build_photo_video()` encodes
     each DISTINCT segment once and assembles the runtime with a concat stream copy, so four photos
     over three hours are four encodes rather than sixty. The keyframe interval is the setting that
     matters and it is not the generated one: `-g fps*10` was tuned on flat vector frames where an
     I-frame is nearly free, and on a photograph it costs 18x the bytes (measured, 180s at
     2560x1440: 57.6 MB against 3.1 MB for one keyframe per segment). CRF stays at 16 — with every
     other frame a skip, the segment IS its keyframe. The audio pass is then the dominant cost of a
     long render, which is the right thing for it to be.

  5g. `--video` burns the slate onto a supplied clip and branches at
     `_run_video()`, ahead of photo mode's branch. One clip in, one clip out,
     camera audio stripped: there is no envelope, no geometry, no playhead and
     no still, so `_video_check()` refuses nearly the whole flag surface and
     only what the slate SAYS and what colour it is survives. The reuse is
     total — `_slate_overlay()` assembles `lss_draw.draw_scrim()` and
     `draw_slate()` into ONE straight-alpha RGBA PNG and ffmpeg composites it
     with a single `overlay`, because *over* is associative and so
     slate-over-scrim-over-frame is the two-pass composite `_photo_frame()`
     already makes onto a still. Two rules are load-bearing and both are
     measured, not argued: the blend happens in **rgb** (`format=yuv420`
     measures peaks of 81 levels on the accent runs, since the chroma plane is
     half resolution exactly where the glyph edges are), and the layer is
     un-premultiplied **once** at the end (0.005 MAE against the Pillow path;
     the ffmpeg `blend` pair that would avoid it measures 0.372, worse, because
     blend truncates where `ImageChops` rounds). The encode settings are the
     one thing photo mode does **not** hand over — see `build_slate_video()`:
     `photo_segment()`'s one-keyframe and free-CRF findings were measured on a
     HELD frame and moving footage inverts both. `_video_legibility()` samples
     across the clip rather than reading one frame, because footage changes
     tone and a slate that reads at second 1 can vanish at second 40 — one
     sample per ~15s, clamped to [5, 24], and the line it prints says "worst of
     N samples" because a sampled minimum read as a guarantee is the way this
     check would mislead.
     The scrim's default is **per mode**, not per flag: `--scrim` parses to
     `None` and `run()` settles it once through `scrim_default()` —
     `SCRIM_DEFAULT` 0.65 for a photo, where one fixed frame makes the wash
     cheap insurance, `VIDEO_SCRIM_DEFAULT` 0.0 for a clip, where a sky that
     already carries the text turns the wash into a haze around it.
     `--slate-position {top,middle,bottom}` moves the slate as a unit, and the
     hook was already there: `draw_slate()` has taken a design-unit `dy` since
     the cover, so the only change needed was that `slate_boxes()` used to
     *derive* `dy` from `dh` internally instead of accepting it — which would
     have left the city ceiling and the legibility check measuring the top of
     the frame while the text sat somewhere else, and nothing in the output
     would have shown it. `slate_dy()` is the one place the mapping lives and
     both the drawing and the measuring go through it. The scrim is the piece
     that did NOT generalise for free: `draw_scrim` was anchored to row 0, and
     is now a band that fades on whichever edges it does not butt against,
     reducing to the old top-down ramp exactly when the slate is at the top.
  5h. An audio file alongside `--video` asks for a LOOPED render — `_run_loop()`, branching ahead of
     `_run_video()` — where the clip repeats to fill the recording and the slate shows only for
     `--slate-intro` / `--slate-outro` minutes at each end. `loop_plan()` is the whole idea and it is
     the analogue of `lss_photo.plan()`: the body is the same clip every time, so it is encoded ONCE
     and every bare repeat names that one file, while a bare stretch inside a partly-slated repeat is
     **stream-copied** out of the body. Measured: a 17.6 min clip under a 159.9 min recording is 12
     concat entries from 4 encoder passes and 2 copies — 21.6 min of footage for a 159.9 min output.
     The copies can be copies because the cut points are known BEFORE the body is encoded and go to
     x264 as `-force_key_frames`, so nothing around a slate boundary is re-encoded; that is the one
     thing to preserve if this is ever restructured. The fourth pass is not slack: when the truncated
     final repeat is shorter than the outro the slate STRADDLES a loop seam, which is two source
     ranges and one repeat that is no longer interchangeable — and it must not fade at that seam,
     since it is one appearance living in two files. `_work` goes to LOCAL temp here, not `outdir`,
     because outdir is routinely a network drive and the scratch is several GB; and `+faststart` is
     deliberately off, being a full rewrite of a 20 GB file for a benefit an upload never collects.
     `_video_legibility(windows=...)` samples only the minutes that show the slate.
  5i. `--vertical` adds a 1080x1920 still for YouTube Shorts beside the thumbnail, and it is a
     LAYOUT, not a crop: `_vertical()` rebuilds the geometry off the same envelope seed, as
     `_cover()` does, for a **720x1280** design frame. 720 rather than 1280 wide is the decision
     everything else follows from: k at 1080px is then 1.5, the landscape thumbnail's own k, so
     every tree, window, star and streak is the same pixel size in both, and a narrower frame holds
     FEWER features across rather than the same number shrunk. So `vlayout()` carries a design
     width too, every count "across the frame" is a density (tree spacing, house and block width,
     stars, clouds and rain per unit area), and the building count is scaled by `dw/1280` before
     `to_levels()`. `wf` is exactly 1.0 at dw == 1280, which is what keeps every landscape and
     cover render byte-identical — and the draw functions take k from each geometry dict's own
     `dw`. The text is a separate layout too: `column_layout()` stacks badge, series, title
     (wrapped to two balanced lines, then shrunk), tagline and a number/clock footer into a
     centred column, and it is the ONE place that positions it — `lss_draw.draw_column()` only
     centres each line at its drawn size, and the city ceiling reads the same result, so there is
     no mirror like `slate_boxes()` to keep in step. It runs in `run()` BEFORE the audio pass,
     because a line that enters `safe_top`/`safe_bottom` is an error naming the preset key and
     the line, and finding that after a three-hour envelope would be the wrong order. The safe
     zones bind the TEXT only; the scenery runs to the bottom edge, less `ground_lift`. Still only:
     a Short is at most 3 minutes, and choosing which minutes of a long recording is its own
     feature. Refused with `--photos` and `--video`.
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
  never a silent fallback. `vlayout(dh)` is the **vertical** layout for a frame
  `dh` design units tall, and the split it encodes is the one rule to know
  before changing anything here: **the ground and the ceiling move with the
  frame; a tree, a house and a window do not.** `DH` stays 720 as the feature
  basis. Every field reduces to the module constant of the same name at
  `dh == 720` exactly - scales are multiplications by 1.0, offsets are
  additions of 0.0 - which is why a square cover cannot disturb a 16:9 render. `sky()` is the one thing here that is **not** silhouette geometry — the
  star field, and the slot a sun or moon will later fill — and it runs on its own RNG stream salted
  off the same seed, so stars can never move a building. `weather()` is a third such stream,
  on its own salt again: clouds sized as a fraction of the band `horizon()` measures, and rain
  as a density over the frame. Nothing in it reads the loudness — the seed varies the weather
  between recordings, the envelope decides the skyline and stops there.
- **`lss_studio.py`** — the Tkinter GUI. Builds the config dict expected by `lss_render.run()` and
  calls it in a background thread, polling a `queue.Queue` on a Tk `after()` timer for log lines and
  progress. Not the place to add render logic — it's a thin form over `lss_render.run()`.
  Settings live on a `ttk.Notebook` of six tabs — Slate, Look, Colour, Shape, Video, Photo — matching the
  argparse groups in `lss_render.main()` (Look and Colour share the `look` group); put a new setting
  in the group that matches what it decides, and in the same group on both sides. The audio/output fields and the Render button,
  Thumbnail only and Preview at controls stay outside the tabs and always visible. Keep the form's
  requested height under ~1000px or it clips on a laptop screen, which is what the tabs are for.
  The notebook sizes to its TALLEST tab, which is why photo mode got a tab of its own rather than
  more rows on Look: a sixth tab costs no height at all (measured: 857px before and after).
  `_photo_apply()` only ever DISABLES, and runs at the end of `_style_changed`/`_colors_changed`/
  `_thumbonly_changed`, so it can never hand back what their finer greying just took away;
  `_photo_mode_changed` is the one that restores, and it restores before calling them.
  `--video` (5g) is deliberately **not** here: it is CLI-only for now, so the usual rule that a
  setting goes in the matching group on both sides does not yet apply to it. Adding it means a
  seventh tab and a fourth thing for `_photo_apply()`'s disable-only discipline to agree with.
  `--vertical` is the "Shorts 9:16" tick beside the cover in the always-visible row, and
  `--badge` is on the Slate tab, typeable only while that tick is on (`_vertical_changed`).
  Both are among what `_photo_apply()` disables. That row sets the window's WIDTH: the tick
  took it from 1046px to 1140px, which is why its label is short. "Let the rain shimmer"
  (`--no-shimmer`) sits under Weather on the Look tab, greyed unless the weather is rain
  (`_weather_changed`); Look is the tallest tab, so it took the window from 866px to 882px.

- **`lss_photo.py`** — photo-background mode: a supplied set of stills cycling on a fixed interval
  in place of the generated skyline. It is a **mode, not a style**, and the reason is the style
  interface itself: every entry in `SCENE_STYLES` must be buildable by `lss_scene.build(style, db,
  lv, ...)`, which is entirely audio-derived geometry, and a photograph has none. `--photos` is the
  switch; `check()` refuses every silhouette and loudness flag rather than ignoring it, on the same
  grounds `lss_scene.check()` gives. Nothing here reads the audio — the recording decides only how
  long the cycle runs.
  Photos are normalised to the output, never the reverse: `load()` applies EXIF orientation *first*
  (Pillow does not, and every measurement after it would be on the wrong axis) then converts to
  sRGB; `frame()` centre-crops and LANCZOS-downscales. Never upscales — `validate()` checks every
  photo against every output the render will write, together and up front, because the cover asks
  3000px on the SHORT edge and refuses stills the video accepts.
  `plan()` is the piece the encode rests on: it splits the runtime into segments and gives every
  full-length segment of a given photo the same KEY, so N photos cost N encodes however long the
  recording is. Only the truncated final segment gets a key of its own.

Supporting pieces:
- **`lss_presets.json`** — user-editable data, not code. Defines named "series" (each with a name,
  accent colour(s), and geometry) and "themes" (seasonal overlays: alternate colours, a name suffix,
  and optionally a colour `cycle` for the playback line). The updater (`lss_update.py`) explicitly
  never overwrites this file, so users can safely add their own presets. Its `layouts` block is
  the slate's positions and sizes — `landscape`, the numbers `draw_slate()` used to hold as
  literals, and `vertical`, the Shorts frame and its safe zones. The same values ship in
  `lss_presets.LAYOUTS`, because a user's copy of this file may predate the block; a layout in
  the file overrides the shipped one field by field. `run()` settles them once, next to `FONT`,
  into `lss_render.LAYOUTS`. Editing `landscape` moves every render, which is what it is for —
  and why the identity check must be run against the shipped values, not a tuned file.
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
- A frame has exactly three colours: `background`, `foreground` and `accent`. `resolve()` and
  `resolve_colors()` in `lss_presets.py` are the only place they are settled, and every caller goes
  through them. An `accent2` in the preset data is **not** a fourth — nothing draws with it; it
  survives only as a companion colour offered in the GUI's colour dropdowns (`palette()`), which
  are likewise derived from the preset data rather than listed by hand. A `stars` key is not a
  fourth either: it holds the *role name* of whichever of the three the star field takes
  (`star_color()`), and its presence is also what says the palette may have stars at all.
  `number_color` (`--number-color`) is not a fourth either: an optional per-render override for
  the slate's number run alone, read in `draw_slate()` and nowhere else, never part of a palette.
  Weather adds no colour either: `lss_draw.weather_tones()` bisects a tone out of the sky and
  the foreground at a target contrast ratio, so a cloud is the palette's own ink heavily washed
  toward its own sky. The direction falls out — the foreground is on the lighter side of the sky
  in eight presets and the darker side in Mist, so Mist's clouds darken with no special case.
- Alpha is the exception, not the rule. Everything draws onto an opaque RGB canvas at `SS` and
  downsamples, which is why `mix()` exists instead of transparency. The places that genuinely
  need alpha are `draw_rain()` (an `L` mask), photo mode's `slate_over()`, and clip mode's
  `_slate_overlay()`. In the last two,
  premultiply and then downsample the coverage as an **L** image and the colour as an **RGB** one,
  SEPARATELY — `lss_draw.premultiplied()` is the one implementation of that rule and both go
  through it. Handing Pillow one premultiplied RGBA image and resizing that is the obvious way to
  write it and it is wrong: LANCZOS' negative lobes push colour above alpha at a glyph edge and the
  composite overshoots into a bright fringe. Measured mean absolute error against the opaque path
  over a flat sky - separate channels 0.15 levels, one RGBA resize 3.3 to 4.6 with peaks past 250.
- Design coordinates are fixed at a 1280x720 basis and scaled by `k = W / 1280.0` everywhere in
  `compose()` — when adjusting layout, change the design-unit constant, not per-resolution numbers.
  The one other basis is the vertical frame's 720 units across (5i): there k is `W / dw`, read
  off `cfg["_dw"]` in `compose()` and off each geometry dict's own `dw` in the draw functions, so
  a new draw function takes k from its geometry, never from a literal 1280.
- `usable()` in `lss_render.py` degrades a configured network drive path (`Z:\...`) to a folder in
  the user's home directory when that drive isn't mounted — this is how the tool stays usable off
  the studio's network.
- Fonts: resolved once at `run()` start via `find_font()`, which checks `LSS_FONT` env var first,
  then falls back through a short hardcoded list (Barlow Condensed Bold preferred, Arial Bold
  fallback on Windows). Separately, a single CHARACTER the slate font lacks (Barlow has no `●`)
  is drawn from `lss_draw.FALLBACK_FONTS` per glyph in `text_run()`/`text_width()`; a character
  the slate font has always uses it, which is what keeps existing slates byte-identical.
