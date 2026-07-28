# tabless — notes for coding agents

A local shelf and reader for HTML that coding agents produce. `README.md` says
what it is for users; this file is what you need to work on it.

## Before changing anything

Read **`CONTRIBUTING.md`** first. It lists seven design constraints that are not
up for negotiation, and **`docs/design-notes.md`** records the failure behind
each one. Both exist because this codebase's hard parts are decisions, not
algorithms — the code is simple and the reasons are not.

The short version, so you recognise when you are about to cross one:

1. One reader window, ever. Projects are tabs.
2. No process management. A `live` entry whose service is down opens blank, on purpose.
3. Copies, never references.
4. No required third-party dependencies (`pyyaml` is optional and must degrade silently).
5. Loopback only.
6. Types stay an open set; nothing in the reader may key off a specific type name.
7. Starring lifts an entry out of its group rather than duplicating it.

## Layout

| | |
|---|---|
| `src/tabless/config.py` | paths and limits. Read them as `config.HOME`, never `from config import HOME` — `reconfigure()` has to be able to reach you |
| `src/tabless/core.py` | storage, index, dependency closure, version folding |
| `src/tabless/projects.py` | project inference and colours |
| `src/tabless/server.py` | the local service, window reuse, page dressing |
| `src/tabless/cli.py` · `client.py` | commands, and talking to the service |
| `src/tabless/reader.html` | the whole front end, one file |
| `src/tabless/locales/*.json` | every user-facing string, shared by CLI and reader |
| `tests/` | one case per historical bug; docstrings say what broke |

## Working on it

```bash
pip install -e .
python -m unittest discover -s tests -v
ruff check .

# Never develop against a real library
export TABLESS_HOME=/tmp/tabless-dev TABLESS_PORT=6181
tabless demo
tabless server                                    # foreground, watch it

# reader.html changed? No restart needed:
curl -X POST http://127.0.0.1:6181/api/reload
```

`server.py` or `core.py` changed → restart the service. `reader.html` → reload.

## House rules

- **Comments explain why, not what.** This codebase is heavily commented on
  purpose: a comment recording a trap is the reason the trap stays fixed. When
  you touch code that has one, keep it accurate rather than deleting it.
- **English in code, always.** Comments, docstrings, identifiers, commit
  messages. User-facing text goes in `locales/*.json` — never inline, never in
  an f-string. Add the key to **every** locale file; the tests enforce parity
  and matching placeholders.
- **Fix a bug, add the test.** And write its docstring as *what went wrong*.
  Three of the bugs fixed for 0.1.0 were found this way rather than by use.
- **Trust CI over your machine.** The matrix is nine jobs because the two places
  this tool touches the platform disagree by design. A symlink bug passed
  locally on Windows and failed on all nine hosted runners.
- Do not `Invoke-Item` / `open` an HTML file to show it to someone. That is the
  habit this project exists to replace.
