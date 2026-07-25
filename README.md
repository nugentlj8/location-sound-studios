# Location Sound Studios

Turns a field-recording audio file into a matching YouTube thumbnail and a
full-length video whose skyline is drawn from the recording's own loudness.

## Install (Windows, once)

1. Install **Python** from [python.org](https://www.python.org/downloads/).
   On the first installer screen, tick **"Add Python to PATH."**
2. Download this repository: green **Code** button → **Download ZIP**, then
   unzip it somewhere permanent like `Documents\LSS`.
3. Double-click **`Setup.bat`**. It installs ffmpeg and the two Python
   packages. If it installs ffmpeg, close the window and run it once more.

Then launch any time with **`Location Sound Studios.bat`**.

### Font (recommended)

Install **Barlow Condensed** (Bold) from Google Fonts, then in Command Prompt:

```
setx LSS_FONT "C:\Users\YOU\AppData\Local\Microsoft\Windows\Fonts\BarlowCondensed-Bold.ttf"
```

Open a fresh window afterward. Without it the app falls back to Arial.

## Updates

The app checks this repository for a newer version each time it opens. If one
exists it asks whether to update; if you say yes it downloads the new files and
uses them next launch. If GitHub is unreachable it just runs the copy you have.

Your `lss_presets.json` is **never** overwritten by an update, so any presets
you add stay yours. The previous version is kept in `lss_studio/previous/`; to
roll back, run `py lss_studio\lss_update.py --rollback` from the repo folder.

## Files

```
Location Sound Studios.bat   launch
Setup.bat                    one-time setup
lss_studio/
  lss_studio.py              the window
  lss_render.py              render engine
  lss_draw.py                drawing
  lss_presets.py             preset loading
  lss_presets.json           YOUR presets (edit freely)
  lss_update.py              self-update
  VERSION                    current version
```

See `RELEASING.md` for how to publish an update.
