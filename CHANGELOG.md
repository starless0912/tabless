# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Media referenced only from JavaScript was never archived.** Blind-eval and
  comparison pages routinely keep their clip list in a script-side data array
  and build the `<video>` grid at runtime. The dependency scan read only HTML
  attributes and CSS `url()`, so such a page archived as a self-contained-looking
  `page` while its media stayed behind — in the worst case in a scratch
  directory about to be cleaned. Quoted strings ending in a media extension now
  count as candidate references, kept only when they resolve to a real file,
  and script files are followed the way stylesheets always were.
- **Re-adding an unchanged file could not repair its snapshot.** The
  identical-content short-circuit judged by entry bytes alone, so after a
  scanner improvement the designated repair path — re-adding the same file —
  was silently refused while reporting success. The short-circuit now also
  requires the dependency closure to be unchanged.

## [0.1.0] — 2026-07-27

First public release. tabless ran privately for some months before this; the
work in this release was making it fit for other people's machines.

### Added

- `tabless add` — archive an HTML file under `<project>/<type>/` and push it to
  the reader. Page vs site is decided by the entry file's dependency closure.
- `tabless live` — register a prototype still under change as a pointer.
- `tabless scan` — adopt HTML already lying around, grouping linked pages into
  one site entry per connected component.
- `tabless list` / `projects` / `types` / `retype` / `open` / `where` / `demo`.
- Single-window reader: project tabs, collapsible type groups, cross-type
  starring, unread tracking, search, keyboard navigation, copy-path-for-agent.
- Version folding with count and size ceilings on retained history.
- English and Chinese throughout — CLI, reader and error pages — resolved from
  `TABLESS_LANG` or the system locale, from one shared string table.
- Optional `projects.toml` for project inference, including reversing coding
  agent scratchpad paths back to the project that produced them.
- Cross-platform support: Windows, macOS and Linux, tested in CI on Python
  3.11–3.13.
- 91 tests covering the traps listed in [docs/design-notes.md](docs/design-notes.md).

### Fixed

Three bugs that predate this release, all found by the work of making it
public rather than by using it.

- **Version folding could delete its own destination.** After moving the old
  copy into history it removed the vacated type folder, without checking that
  folder was not also where the new copy was about to be written. Only bites
  when the folded document is the only one of its type, which is why it stayed
  hidden — in a busy library that `rmdir` almost always fails as non-empty.
  Found while writing the test suite.
- **A dependency closure spanning two drives failed the whole archive.**
  With no common root, the fallback took an arbitrary member of the closure,
  which can be the dependency rather than the entry — leaving the entry with no
  relative path to record. Now anchored on the entry's own directory, so at
  worst one unreachable asset is skipped.
- **Project inference did not see through symlinks.** `add_document` resolves
  the file it is handed while the project table holds whatever was typed, so on
  macOS (`/tmp` and `/var` are symlinks into `/private`) every document landed
  in `_inbox` — silently. Caught by CI, having passed on the author's machine.

[Unreleased]: https://github.com/starless0912/tabless/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/starless0912/tabless/releases/tag/v0.1.0
