# Contributing

Bug reports, questions and pull requests are all welcome. This file covers the
practical bits and, more importantly, the handful of constraints that are not
up for negotiation.

## Getting set up

```bash
git clone https://github.com/starless0912/tabless
cd tabless
pip install -e .

python -m unittest discover -s tests -v
ruff check .
```

No dev dependencies beyond `ruff` for linting. Tests are plain `unittest` from
the standard library and each one builds its own throwaway library in a temp
directory, so they never touch yours.

Working on the reader or the service:

```bash
# Point at a scratch library so you never experiment on real documents
export TABLESS_HOME=/tmp/tabless-dev
export TABLESS_PORT=6181

tabless demo                 # something to look at
tabless server               # run in the foreground and watch it

# reader.html changed? No restart needed — tell open windows to refresh:
curl -X POST http://127.0.0.1:6181/api/reload
```

## Design constraints

These are not style preferences. Each one is here because the alternative was
tried and produced a specific, remembered failure — the long version is in
[docs/design-notes.md](docs/design-notes.md). A pull request that changes one of
these needs to address the failure, not just the code.

1. **One reader window, ever.** Projects are tabs. No entry point may spawn a
   second window. Several windows only trade a pile of tabs for a pile of
   windows: that is sorting, not reducing, and reducing was the point.

2. **No process management.** tabless does not supervise anything, start your
   dev server, or watch ports. A `live` entry whose service isn't running opens
   blank, on purpose. Cross this line and it becomes a launcher that has to
   understand every project's toolchain.

3. **Copies, never references.** An index of paths is dead links within three
   months. If it is in the library, the library has its own copy.

4. **No required third-party dependencies.** `pyyaml` is optional and only for
   reading an external project table; missing it must degrade gracefully, never
   crash. An on-demand background process should not carry a web framework's
   startup cost, and "works from a clone with nothing installed" is a feature.

5. **Loopback only.** `127.0.0.1`. Do not make the bind address configurable —
   this serves arbitrary local HTML with no authentication, and it is safe
   precisely because it cannot be reached.

6. **Types stay an open set.** `--type` takes whatever it is given; there is no
   whitelist. The alias table exists to fold obvious synonyms, not to restrict.
   And **nothing in the reader may key off a specific type name** — unknown
   types fall back to their raw name and a generic icon, never to a broken
   group.

7. **Starring pins across types, in one place.** A starred entry is lifted out
   of its type group, not duplicated into two. The same document appearing
   twice in one list is worse than it being sorted badly.

## Pull requests

- Add a test when you fix a bug. The suite is deliberately a catalogue of
  things that actually broke; a docstring saying *what went wrong* is worth more
  than one describing what the function does.
- Keep the comments explaining **why**. This codebase is unusually heavily
  commented on purpose — the reasoning is most of what makes it maintainable,
  and a comment that records a trap is the reason the trap stays fixed.
- Comments, docstrings and identifiers are in English. User-facing strings
  belong in `src/tabless/locales/*.json`, never inline. Add a key to **every**
  locale file; the test suite enforces parity and matching placeholders.
- Run `ruff check .` and the tests. CI runs both across Linux, macOS and
  Windows on Python 3.11–3.13.

## Adding a language

1. Copy `src/tabless/locales/en.json` to `<code>.json` and translate the values.
   Keep every key and every `{placeholder}` — `tests/test_i18n.py` checks both.
2. That is all. `i18n.available()` discovers the file, the CLI picks it up, and
   the service hands it to the reader. Locale tags reduce to the file's stem, so
   `pt_BR` finds `pt.json`.
3. If your locale needs a different resolution rule (the way `zh` also matches
   Windows' "Chinese (Simplified)_China"), add it to `i18n.lang()` with a test.

## Reporting a bug

`tabless where` output plus your OS and Python version covers most of it. If a
document renders wrongly, whether it is a `page` or a `site` is usually the
first useful clue — `tabless list` shows `▤` for sites.
