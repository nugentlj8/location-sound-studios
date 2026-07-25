# Publishing an update

The whole update mechanism is: bump a version number, push to GitHub. Every
copy of the app picks it up on next launch.

## One-time repo setup

1. Make a **public** repo on GitHub named `location-sound-studios`.
2. Open `lss_studio/lss_update.py` and set the two lines at the top:
   ```python
   GITHUB_USER = "your-username"
   GITHUB_REPO = "location-sound-studios"
   ```
3. Push everything:
   ```
   git init
   git add .
   git commit -m "Initial version 1.0.0"
   git branch -M main
   git remote add origin https://github.com/your-username/location-sound-studios.git
   git push -u origin main
   ```

## Shipping a change

1. Edit whatever needs editing in `lss_studio/`.
2. Bump the version in **both** `VERSION` files — the one at the repo root and
   `lss_studio/VERSION` (the app reads the latter). Use plain numbers:
   `1.0.1` for a fix, `1.1.0` for a feature.
3. Commit and push:
   ```
   git add .
   git commit -m "1.0.1 - fix clock alignment"
   git push
   ```

That's it. Next time anyone opens the app it sees the higher version and offers
the update.

## Version numbers

`MAJOR.MINOR.PATCH` — bump PATCH for fixes, MINOR for features, MAJOR for a
change that breaks old render files. The updater compares them numerically, so
`1.10.0` correctly counts as newer than `1.9.0`.

## Keeping older versions

Every push is already a point in history. To pull an old version back:

```
git log --oneline           # find the commit
git checkout <hash> -- lss_studio/
```

Or tag releases so they're easy to find:

```
git tag v1.0.0
git push --tags
```

## What updates and what doesn't

The updater replaces the program files and `VERSION`. It never touches
`lss_presets.json`, so users keep their own presets. If you add new default
presets, ship them as new entries the app writes on first run, or document
them so users can paste them in — don't rely on overwriting their JSON.
