# tools

Development scripts. **Not part of the app** — `lss_update.py` ships only what is
in `lss_studio/`, so nothing here reaches an installed copy.

All of them write into `renders/`, which is gitignored, and regenerate their own
test audio if it is not there.

| | |
|---|---|
| `identity_check.py` | renders 90 style/palette/state combinations and SHAs them, to prove a change left every existing style byte-for-byte identical |
| `stars_preview.py` | the 24-still star-field review set — both styles, both playback states, every palette that allows a field |
| `stars_fade_options.py` | three star fade depths on one 30-second clip, so the depth can be chosen against a real encode |

## Proving a change renders identically

Run the baseline **from the commit you are changing away from**, then again with
your change applied:

```
git stash                              # or check out the old commit
py tools/identity_check.py baseline
git stash pop
py tools/identity_check.py after
py tools/identity_check.py compare
```

The SHA files land in `renders/` rather than being committed: they depend on
which fonts this machine has, so a checked-in baseline would be misleading
somewhere else. Regenerate it from the old commit instead.
