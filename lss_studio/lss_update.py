"""Self-update: pull the latest program files from GitHub when online.

Design goals:
  - Never block the app. If GitHub is unreachable, we run the local copy.
  - Never half-update. Download everything to a temp dir, verify, then swap.
  - Keep the previous version so a bad update can be rolled back.

This uses only the Python standard library (urllib) so there is nothing extra
to install. It talks to GitHub's raw-content endpoint, which needs no token for
a public repo.
"""

import json
import os
import shutil
import ssl
import sys
import tempfile
import urllib.request

# ---- EDIT THESE TWO LINES when you create the repo -------------------------
GITHUB_USER = "nugentlj8"
GITHUB_REPO = "location-sound-studios"
BRANCH = "main"
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{BRANCH}/lss_studio"

# Files that make up the program. The updater replaces exactly these.
FILES = ["lss_render.py", "lss_draw.py", "lss_photo.py", "lss_scene.py",
         "lss_presets.py", "lss_studio.py", "lss_update.py", "VERSION"]

# lss_presets.json is intentionally NOT auto-updated: the user edits it to add
# their own presets, and we must never overwrite their work. New default
# presets ship in code; the JSON stays the user's own.


def _get(url, timeout=8):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "LSS-Updater"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def local_version():
    try:
        with open(os.path.join(HERE, "VERSION")) as fh:
            return fh.read().strip()
    except OSError:
        return "0.0.0"


def _vtuple(v):
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def check(timeout=8):
    """Return the remote version string if it is newer, else None."""
    try:
        remote = _get(f"{RAW}/VERSION", timeout).decode().strip()
    except Exception:
        return None
    if _vtuple(remote) > _vtuple(local_version()):
        return remote
    return None


def update(log=print):
    """Download the new version into place. Returns True on success.
    Keeps the prior version in a 'previous' folder for rollback."""
    tmp = tempfile.mkdtemp(prefix="lss_upd_")
    try:
        # 1. download everything first; a failure here touches nothing live
        for name in FILES:
            data = _get(f"{RAW}/{name}")
            with open(os.path.join(tmp, name), "wb") as fh:
                fh.write(data)
        # 2. sanity check: the Python files must at least compile
        import py_compile
        for name in FILES:
            if name.endswith(".py"):
                py_compile.compile(os.path.join(tmp, name), doraise=True)
        # 3. back up the current version
        prev = os.path.join(HERE, "previous")
        shutil.rmtree(prev, ignore_errors=True)
        os.makedirs(prev, exist_ok=True)
        for name in FILES:
            src = os.path.join(HERE, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(prev, name))
        # 4. swap in the new files
        for name in FILES:
            shutil.copy2(os.path.join(tmp, name), os.path.join(HERE, name))
        log("Updated to version " + local_version())
        return True
    except Exception as e:
        log("Update failed, keeping current version. (" + str(e) + ")")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def missing():
    """Files this install should have but does not.

    update() fetches the list the RUNNING updater declares, so a release that
    ADDS a module cannot deliver it in the same hop: the new lss_update.py
    lands in that update, and only then does this install know the file exists
    at all. Until 1.11.0 nothing had ever been added to FILES, so the gap had
    never opened - and it opens silently, because py_compile verifies each file
    on its own and never executes an import.

    This is what closes it, on the launch after. It is deliberately driven off
    the LOCAL list rather than a list fetched from GitHub: the remote is then
    only ever asked for files this installed code already names, so a bad
    remote cannot introduce a filename of its own.
    """
    return [n for n in FILES if not os.path.exists(os.path.join(HERE, n))]


def repair(log=print, timeout=8):
    """Fetch whatever missing() reports. Returns the names actually restored.

    Same order as update(): download and verify everything first, then move it
    into place, so a half-finished repair never lands.
    """
    names = missing()
    if not names:
        return []
    tmp = tempfile.mkdtemp(prefix="lss_fix_")
    try:
        import py_compile
        for name in names:
            path = os.path.join(tmp, name)
            with open(path, "wb") as fh:
                fh.write(_get(f"{RAW}/{name}", timeout))
            if name.endswith(".py"):
                py_compile.compile(path, doraise=True)
        for name in names:
            shutil.copy2(os.path.join(tmp, name), os.path.join(HERE, name))
        log("Restored missing file(s): " + ", ".join(names))
        return names
    except Exception as e:
        log("Could not restore missing files. (" + str(e) + ")")
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def rollback(log=print):
    """Restore the previous version if one was kept."""
    prev = os.path.join(HERE, "previous")
    if not os.path.isdir(prev):
        log("No previous version to roll back to.")
        return False
    for name in FILES:
        src = os.path.join(prev, name)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(HERE, name))
    log("Rolled back to version " + local_version())
    return True


if __name__ == "__main__":
    if "--rollback" in sys.argv:
        rollback()
    elif "--repair" in sys.argv:
        print("Missing:", ", ".join(missing()) or "nothing")
        repair()
    else:
        newer = check()
        if newer:
            print(f"New version {newer} available (have {local_version()}).")
            update()
        else:
            print(f"Up to date (version {local_version()}).")
            if missing():
                repair()
